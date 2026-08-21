#!/usr/bin/env python3
"""Hit a running opendesign2api and check models + chat + SSE."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("OPENDESIGN2API_BASE", "http://127.0.0.1:8792").rstrip("/")
MODEL = os.environ.get("OPENDESIGN2API_MODEL", "deepseek-v4-flash")
KEY = os.environ.get("OPENDESIGN2API_KEY", "").strip()


def headers(accept: str = "application/json") -> dict[str, str]:
    out = {"Content-Type": "application/json", "Accept": accept}
    if KEY:
        out["Authorization"] = "Bearer " + KEY
    return out


def get(path: str) -> tuple[int, bytes]:
    req = urllib.request.Request(BASE + path, headers=headers())
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def post(path: str, payload: dict, accept: str = "application/json") -> tuple[int, bytes]:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers(accept),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def main() -> int:
    failed = 0

    try:
        code, body = get("/healthz")
    except urllib.error.URLError as exc:
        print("FAIL connect=%s" % exc.reason)
        return 1
    print("healthz", code, body.decode("utf-8", "replace")[:200])
    if code != 200:
        failed += 1

    code, body = get("/v1/models")
    try:
        models = json.loads(body)
        ids = [m.get("id") for m in models.get("data") or []]
    except json.JSONDecodeError:
        ids = []
        failed += 1
    print("models", code, "count=%s has_model=%s" % (len(ids), MODEL in ids))
    if code != 200 or MODEL not in ids:
        failed += 1

    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Reply with exactly: LOCAL_OK"}],
        "max_tokens": 32,
        "stream": False,
    }
    code, body = post("/v1/chat/completions", payload)
    try:
        obj = json.loads(body)
        content = (((obj.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    except json.JSONDecodeError:
        content = ""
        obj = {}
    print("chat", code, "content=%r model=%s" % (content, obj.get("model")))
    if code != 200 or "LOCAL_OK" not in content:
        failed += 1
        print("chat body", body[:500])

    payload["stream"] = True
    payload["messages"] = [{"role": "user", "content": "Reply with exactly: STREAM_OK"}]
    code, body = post("/v1/chat/completions", payload, accept="text/event-stream")
    assembled = []
    for block in body.decode("utf-8", "replace").split("\n\n"):
        for line in block.splitlines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data in ("", "[DONE]"):
                continue
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            delta = ((obj.get("choices") or [{}])[0].get("delta") or {})
            piece = delta.get("content")
            if piece:
                assembled.append(piece)
    joined = "".join(assembled)
    got_content = "STREAM_OK" in joined
    print("stream", code, "bytes=%s assembled=%r" % (len(body), joined))
    if code != 200 or not got_content:
        failed += 1
        print("stream head", body[:600])

    if failed:
        print("FAIL checks=%s" % failed)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
