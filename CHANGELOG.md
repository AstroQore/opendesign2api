# Changelog

All notable changes to this project are documented here.

## 0.1.0 - 2026-08-21

- Proxy the official OpenDesign Link Models and Chat Completions endpoints.
- Preserve non-streaming JSON and streaming SSE responses.
- Read and hot-reload the official Vela profile without copying credentials into the
  repository.
- Add optional local Bearer authentication, constant-time key comparison, request
  size limits, client/upstream timeouts, log sanitization, and upstream-key redaction.
- Add Docker, Compose, systemd, CPA integration documentation, CI, GHCR publishing,
  and synthetic-credential tests.
