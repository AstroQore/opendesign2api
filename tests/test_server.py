from __future__ import annotations

import http.client
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import server  # noqa: E402


class FakeResponse:
    def __init__(self, chunks: list[bytes], content_type: str = "application/json") -> None:
        self.status = 200
        self.headers = {"Content-Type": content_type}
        self._chunks = list(chunks)

    def read(self, _size: Optional[int] = None) -> bytes:
        if not self._chunks:
            return b""
        if _size is None:
            payload = b"".join(self._chunks)
            self._chunks.clear()
            return payload
        return self._chunks.pop(0)


class DelayedSSEHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(
            b'data: {"model":"test-model","choices":[{"delta":{"content":"FIRST"}}]}\n\n'
        )
        self.wfile.flush()
        self.server.first_sent.set()  # type: ignore[attr-defined]
        self.server.release.wait(timeout=5)  # type: ignore[attr-defined]
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


class GatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tempdir.name) / "config.json"
        self.config_path.write_text(
            json.dumps(
                {
                    "profiles": {
                        "prod": {
                            "runtimeKey": "synthetic-upstream-key",
                            "linkUrl": "https://upstream.invalid",
                        }
                    }
                }
            )
        )
        self.env = mock.patch.dict(
            os.environ,
            {
                "OPENDESIGN2API_CONFIG": str(self.config_path),
                "OPENDESIGN2API_PROFILE": "prod",
                "OPENDESIGN2API_KEY": "synthetic-local-key",
                "OPENDESIGN2API_MAX_REQUEST_BYTES": "8388608",
            },
            clear=False,
        )
        self.env.start()
        self.original_creds = server.CREDS
        server.CREDS = server.CredentialStore()
        self.httpd = server.GatewayServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        server.CREDS = self.original_creds
        self.env.stop()
        self.tempdir.cleanup()

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        response_headers = dict(response.getheaders())
        connection.close()
        return response.status, response_headers, payload

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer synthetic-local-key"}

    def test_health_reads_profile_without_calling_upstream(self) -> None:
        status, _, payload = self.request("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["ok"])

    def test_models_requires_local_key_and_hides_upstream_key(self) -> None:
        status, _, payload = self.request("GET", "/v1/models")
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(payload)["error"]["code"], "invalid_api_key")

        fake = FakeResponse(
            [
                b'{"object":"list","data":['
                b'{"id":"test-model"},{"id":"another-model"}]}'
            ]
        )
        with mock.patch.object(server.urllib.request, "urlopen", return_value=fake) as opened:
            status, _, payload = self.request("GET", "/v1/models", headers=self.auth_headers())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["object"], "list")
        ids = [item["id"] for item in json.loads(payload)["data"]]
        self.assertEqual(ids, ["test-model", "another-model"])
        upstream_request = opened.call_args.args[0]
        self.assertEqual(upstream_request.get_header("Authorization"), "Bearer synthetic-upstream-key")
        self.assertNotIn(b"synthetic-upstream-key", payload)

    def test_non_streaming_chat_is_forwarded(self) -> None:
        upstream_payload = {
            "id": "chatcmpl-test",
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "OK"}}],
        }
        fake = FakeResponse([json.dumps(upstream_payload).encode()])
        body = json.dumps(
            {
                "model": "test-model",
                "messages": [{"role": "user", "content": "ping"}],
            }
        ).encode()
        headers = {**self.auth_headers(), "Content-Type": "application/json"}
        with mock.patch.object(server.urllib.request, "urlopen", return_value=fake) as opened:
            status, response_headers, payload = self.request(
                "POST", "/v1/chat/completions", body=body, headers=headers
            )
        self.assertEqual(status, 200)
        self.assertEqual(response_headers["Content-Type"], "application/json")
        self.assertEqual(json.loads(payload)["choices"][0]["message"]["content"], "OK")
        self.assertEqual(json.loads(payload)["model"], "test-model")
        upstream_body = json.loads(opened.call_args.args[0].data)
        self.assertEqual(upstream_body["model"], "test-model")

    def test_upstream_error_redacts_runtime_key(self) -> None:
        upstream_error = server.urllib.error.HTTPError(
            "https://upstream.invalid/v1/models",
            401,
            "Unauthorized",
            {"Content-Type": "application/json"},
            io.BytesIO(b'{"error":{"message":"synthetic-upstream-key rejected"}}'),
        )
        with mock.patch.object(server.urllib.request, "urlopen", side_effect=upstream_error):
            status, _, payload = self.request("GET", "/v1/models", headers=self.auth_headers())
        self.assertEqual(status, 401)
        self.assertNotIn(b"synthetic-upstream-key", payload)
        self.assertIn(b"[REDACTED]", payload)

    def test_streaming_chat_flushes_all_sse_chunks(self) -> None:
        chunks = [
            b'data: {"model":"test-model","choices":[{"delta":{"content":"ST"}}]}\n\n',
            b'data: {"model":"test-model","choices":[{"delta":{"content":"REAM"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        fake = FakeResponse(chunks, "text/event-stream")
        body = json.dumps(
            {
                "model": "test-model",
                "messages": [{"role": "user", "content": "ping"}],
                "stream": True,
            }
        ).encode()
        headers = {**self.auth_headers(), "Content-Type": "application/json"}
        with mock.patch.object(server.urllib.request, "urlopen", return_value=fake):
            status, response_headers, payload = self.request(
                "POST", "/v1/chat/completions", body=body, headers=headers
            )
        self.assertEqual(status, 200)
        self.assertEqual(response_headers["Content-Type"], "text/event-stream")
        self.assertIn(b'"content":"ST"', payload)
        self.assertIn(b'"model":"test-model"', payload)
        self.assertTrue(payload.endswith(b"data: [DONE]\n\n"))

    def test_streaming_forwards_first_event_before_upstream_finishes(self) -> None:
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), DelayedSSEHandler)
        upstream.first_sent = threading.Event()  # type: ignore[attr-defined]
        upstream.release = threading.Event()  # type: ignore[attr-defined]
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        self.config_path.write_text(
            json.dumps(
                {
                    "profiles": {
                        "prod": {
                            "runtimeKey": "synthetic-upstream-key",
                            "linkUrl": "http://127.0.0.1:%s" % upstream.server_port,
                        }
                    }
                }
            )
        )
        server.CREDS = server.CredentialStore()
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        body = json.dumps(
            {
                "model": "test-model",
                "messages": [{"role": "user", "content": "ping"}],
                "stream": True,
            }
        ).encode()
        headers = {**self.auth_headers(), "Content-Type": "application/json"}
        try:
            connection.request("POST", "/v1/chat/completions", body=body, headers=headers)
            response = connection.getresponse()
            first_line: list[bytes] = []
            forwarded = threading.Event()

            def read_first_line() -> None:
                first_line.append(response.readline())
                forwarded.set()

            reader = threading.Thread(target=read_first_line, daemon=True)
            reader.start()
            upstream_started = upstream.first_sent.wait(timeout=1)  # type: ignore[attr-defined]
            forwarded_before_release = forwarded.wait(timeout=1)
            upstream.release.set()  # type: ignore[attr-defined]
            reader.join(timeout=2)
            response.read()
            self.assertTrue(upstream_started)
            self.assertTrue(forwarded_before_release, "gateway buffered SSE until upstream EOF")
            self.assertIn(b'"content":"FIRST"', first_line[0])
            self.assertIn(b'"model":"test-model"', first_line[0])
        finally:
            upstream.release.set()  # type: ignore[attr-defined]
            connection.close()
            upstream.shutdown()
            upstream.server_close()
            upstream_thread.join(timeout=5)

    def test_invalid_json_and_oversized_body_fail_before_upstream(self) -> None:
        headers = {**self.auth_headers(), "Content-Type": "application/json"}
        status, _, payload = self.request(
            "POST", "/v1/chat/completions", body=b"not-json", headers=headers
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(payload)["error"]["type"], "invalid_request_error")

        with mock.patch.dict(os.environ, {"OPENDESIGN2API_MAX_REQUEST_BYTES": "4"}):
            status, _, payload = self.request(
                "POST", "/v1/chat/completions", body=b"12345", headers=headers
            )
        self.assertEqual(status, 413)
        self.assertEqual(json.loads(payload)["error"]["code"], "request_too_large")

    def test_log_values_cannot_inject_new_lines(self) -> None:
        self.assertEqual(server.safe_log_value("model\r\nforged"), "model??forged")


if __name__ == "__main__":
    unittest.main()
