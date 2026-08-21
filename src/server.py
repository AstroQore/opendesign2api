#!/usr/bin/env python3
"""Local OpenAI-compatible gateway for OpenDesign Cloud (Vela / AMR Link).

Reads the official `vela login` session at ~/.amr/config.json and proxies
to https://amr-link.open-design.ai/v1/{models,chat/completions}.
"""

from __future__ import annotations

import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional, Tuple

VERSION = "0.1.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8792
DEFAULT_PROFILE = "prod"
DEFAULT_UPSTREAM_TIMEOUT_S = 600
DEFAULT_MAX_REQUEST_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_UPSTREAM_ERROR_BYTES = 64 * 1024
DEFAULT_CLIENT_TIMEOUT_S = 30
USER_AGENT = "opendesign2api/0.1"


class CredentialError(RuntimeError):
    pass


class CredentialStore:
    def __init__(self) -> None:
        self._mtime: Optional[float] = None
        self._runtime_key = ""
        self._link_url = ""
        self._path = Path()

    def _config_path(self) -> Path:
        raw = os.environ.get("OPENDESIGN2API_CONFIG", "").strip()
        if raw:
            return Path(raw).expanduser()
        return Path.home() / ".amr" / "config.json"

    def load(self) -> Tuple[str, str]:
        path = self._config_path()
        try:
            mtime = path.stat().st_mtime
        except OSError as exc:
            raise CredentialError(
                "missing ~/.amr/config.json; run Open Design desktop or `vela login`"
            ) from exc
        if self._mtime == mtime and self._runtime_key and self._link_url:
            return self._runtime_key, self._link_url
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise CredentialError("cannot read AMR config") from exc
        profile = os.environ.get("OPENDESIGN2API_PROFILE", DEFAULT_PROFILE).strip() or DEFAULT_PROFILE
        stored = (data.get("profiles") or {}).get(profile)
        if not isinstance(stored, dict):
            raise CredentialError("AMR profile %s not found" % profile)
        runtime_key = str(stored.get("runtimeKey") or "").strip()
        link_url = str(stored.get("linkUrl") or "").strip().rstrip("/")
        if not runtime_key or not link_url:
            raise CredentialError("AMR profile %s is missing runtimeKey/linkUrl" % profile)
        self._mtime = mtime
        self._runtime_key = runtime_key
        self._link_url = link_url
        self._path = path
        return runtime_key, link_url


CREDS = CredentialStore()


def openai_error(message: str, status: int, err_type: str = "api_error", code: Optional[str] = None) -> bytes:
    payload: dict[str, Any] = {
        "error": {
            "message": message,
            "type": err_type,
            "param": None,
            "code": code,
        }
    }
    return json.dumps(payload).encode("utf-8")


def local_key() -> str:
    return os.environ.get("OPENDESIGN2API_KEY", "").strip()


def positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def safe_log_value(value: object, limit: int = 160) -> str:
    raw = str(value or "-")
    cleaned = "".join(char if char >= " " and char != "\x7f" else "?" for char in raw)
    return cleaned[:limit] or "-"


def redact_text(value: object, secret: str) -> str:
    text = str(value)
    return text.replace(secret, "[REDACTED]") if secret else text


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "opendesign2api/" + VERSION

    def setup(self) -> None:
        super().setup()
        timeout = positive_int_env("OPENDESIGN2API_CLIENT_TIMEOUT", DEFAULT_CLIENT_TIMEOUT_S)
        self.connection.settimeout(timeout)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _unauthorized(self) -> None:
        self._send(
            401,
            openai_error("invalid local api key", 401, "invalid_request_error", "invalid_api_key"),
        )

    def _check_local_auth(self) -> bool:
        expected = local_key()
        if not expected:
            return True
        auth = (self.headers.get("Authorization") or "").strip()
        if hmac.compare_digest(auth, "Bearer " + expected):
            return True
        self._unauthorized()
        return False

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/health", "/healthz"):
            try:
                CREDS.load()
                ready = True
                detail = "ok"
            except CredentialError as exc:
                ready = False
                detail = str(exc)
            status = 200 if ready else 503
            body = json.dumps(
                {
                    "ok": ready,
                    "service": "opendesign2api",
                    "version": VERSION,
                    "detail": detail,
                }
            ).encode("utf-8")
            self._send(status, body)
            return
        if path in ("/v1/models", "/models"):
            if not self._check_local_auth():
                return
            self._proxy("GET", "/v1/models", None)
            return
        self._send(404, openai_error("unknown path %s" % path, 404, "invalid_request_error", "not_found"))

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path not in ("/v1/chat/completions", "/chat/completions"):
            self._send(404, openai_error("unknown path %s" % path, 404, "invalid_request_error", "not_found"))
            return
        if not self._check_local_auth():
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self._send(400, openai_error("invalid Content-Length", 400, "invalid_request_error"))
            return
        max_bytes = positive_int_env("OPENDESIGN2API_MAX_REQUEST_BYTES", DEFAULT_MAX_REQUEST_BYTES)
        if length < 0:
            self._send(400, openai_error("invalid Content-Length", 400, "invalid_request_error"))
            return
        if length > max_bytes:
            self._send(
                413,
                openai_error(
                    "request body exceeds %s bytes" % max_bytes,
                    413,
                    "invalid_request_error",
                    "request_too_large",
                ),
            )
            return
        try:
            body = self.rfile.read(length) if length > 0 else b"{}"
        except TimeoutError:
            self._send(
                408,
                openai_error(
                    "timed out reading request body",
                    408,
                    "invalid_request_error",
                    "request_timeout",
                ),
            )
            return
        self._proxy("POST", "/v1/chat/completions", body)

    def _proxy(self, method: str, upstream_path: str, body: Optional[bytes]) -> None:
        started = time.time()
        model = None
        stream = False
        if body:
            try:
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    model = parsed.get("model")
                    stream = bool(parsed.get("stream"))
            except json.JSONDecodeError:
                self._send(400, openai_error("request body must be JSON", 400, "invalid_request_error"))
                return
        try:
            runtime_key, link_url = CREDS.load()
        except CredentialError as exc:
            self._send(503, openai_error(str(exc), 503, "api_error", "missing_credentials"))
            return

        url = link_url + upstream_path
        headers = {
            "Authorization": "Bearer " + runtime_key,
            "Accept": "text/event-stream" if stream else "application/json",
            "User-Agent": USER_AGENT,
            "HTTP-Referer": "https://open-design.ai",
            "X-Title": "opendesign2api",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            timeout = positive_int_env("OPENDESIGN2API_UPSTREAM_TIMEOUT", DEFAULT_UPSTREAM_TIMEOUT_S)
            resp = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            err_body = exc.read(DEFAULT_MAX_UPSTREAM_ERROR_BYTES + 1)
            content_type = exc.headers.get("Content-Type") or "application/json"
            if len(err_body) > DEFAULT_MAX_UPSTREAM_ERROR_BYTES:
                err_body = openai_error(
                    "upstream error body exceeded the relay limit",
                    exc.code,
                    "api_error",
                    "upstream_error_too_large",
                )
                content_type = "application/json"
            elif err_body:
                err_body = err_body.replace(runtime_key.encode("utf-8"), b"[REDACTED]")
            else:
                reason = redact_text(exc.reason or "upstream error", runtime_key)
                err_body = openai_error(reason, exc.code)
            self.log_message(
                "%s %s model=%s upstream=%s %sms",
                method,
                upstream_path,
                safe_log_value(model),
                exc.code,
                int((time.time() - started) * 1000),
            )
            self._send(exc.code, err_body, content_type)
            return
        except Exception as exc:  # noqa: BLE001
            detail = safe_log_value(redact_text(exc, runtime_key), 240)
            self.log_message("upstream failed: %s", detail)
            self._send(502, openai_error("upstream request failed: %s" % detail, 502, "api_error", "upstream"))
            return

        content_type = resp.headers.get("Content-Type") or (
            "text/event-stream" if stream else "application/json"
        )
        self.send_response(resp.status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        if not stream:
            payload = resp.read()
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.end_headers()
            try:
                read_chunk = getattr(resp, "read1", None)
                if not callable(read_chunk):
                    read_chunk = resp.read
                while True:
                    chunk = read_chunk(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except BrokenPipeError:
                return
        self.log_message(
            "%s %s model=%s upstream=%s stream=%s %sms",
            method,
            upstream_path,
            safe_log_value(model),
            resp.status,
            stream,
            int((time.time() - started) * 1000),
        )


class GatewayServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    host = os.environ.get("OPENDESIGN2API_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST
    try:
        port = int(os.environ.get("OPENDESIGN2API_PORT", str(DEFAULT_PORT)))
    except ValueError:
        sys.stderr.write("OPENDESIGN2API_PORT must be an integer\n")
        return 2
    try:
        CREDS.load()
        cred_ok = "credentials ok"
    except CredentialError as exc:
        cred_ok = str(exc)
    auth_mode = "local key required" if local_key() else "open on loopback"
    sys.stderr.write(
        "opendesign2api %s listening on http://%s:%s/v1  (%s; %s)\n"
        % (VERSION, host, port, auth_mode, cred_ok)
    )
    httpd = GatewayServer((host, port), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nstopped\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
