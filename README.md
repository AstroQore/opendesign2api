# opendesign2api

**Use your OpenDesign Cloud models through an OpenAI-compatible API.**

[![CI](https://github.com/AstroQore/opendesign2api/actions/workflows/ci.yml/badge.svg)](https://github.com/AstroQore/opendesign2api/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![container](https://img.shields.io/badge/ghcr.io-opendesign2api-blue)](https://github.com/AstroQore/opendesign2api/pkgs/container/opendesign2api)

[中文文档](README.zh-CN.md)

OpenDesign's Vela login already points at an official OpenAI-shaped Link endpoint:
`/v1/models` and `/v1/chat/completions`. This project is a small local gateway that
reads the official login profile, keeps its runtime key away from downstream apps,
and forwards both JSON and SSE responses.

It does not scrape a website, reverse-engineer a private wire protocol, or bundle any
credential.

```text
OpenAI client ──► opendesign2api ──► OpenDesign Link
                  127.0.0.1:8792     official /v1 endpoint
```

| Capability | Status |
|---|---|
| `GET /v1/models` | ✅ |
| `POST /v1/chat/completions` | ✅ |
| Streaming chat completions (SSE) | ✅ |
| Optional local Bearer authentication | ✅ |
| Login profile hot reload by file mtime | ✅ |
| `/v1/responses`, Claude Messages, media APIs | ❌ |

## Credential file: read this first

OpenDesign Desktop and `vela login` store the Vela profile at:

```text
~/.amr/config.json
```

The gateway reads `profiles.prod.runtimeKey` and `profiles.prod.linkUrl` from that
file. You can select another profile with `OPENDESIGN2API_PROFILE` or another file
with `OPENDESIGN2API_CONFIG`.

The file is a secret:

- never commit, upload, paste, or bake it into a container image;
- keep it mode `0600` where possible;
- mount it read-only when using Docker;
- copy it to a server only through a secure channel you control;
- replace it after `vela login` refreshes the profile. The gateway notices mtime
  changes on the next request, so a restart is normally unnecessary.

Some agent tools use a file named `auth.json`. **opendesign2api does not need or read
an `auth.json` file.** Treat any such file as a credential too and never add it to
this repository. `.gitignore` excludes common credential filenames, but ignore rules
are not a substitute for checking `git status` before every push.

No real credential is present in this repository, its tests, its image, or its CI.

## Prerequisites

- OpenDesign Desktop or the bundled `vela` CLI
- a successful `vela login`
- `vela model list` showing at least one enabled model
- Python 3.9+ for source installs, or Docker Compose

## Quick start

### From source

```bash
git clone https://github.com/AstroQore/opendesign2api.git
cd opendesign2api

export OPENDESIGN2API_KEY="$(openssl rand -hex 32)"
python3 src/server.py
```

The default credential path is `~/.amr/config.json` and the default listener is
`127.0.0.1:8792`.

In another terminal:

```bash
export OPENDESIGN2API_KEY='<the local bridge key from the server terminal>'
python3 scripts/probe.py
```

The probe checks health, the model catalog, a non-streaming completion, and an SSE
completion. Success ends with `PASS`.

### Docker Compose

```bash
git clone https://github.com/AstroQore/opendesign2api.git
cd opendesign2api
sudo install -d -m 0700 /var/lib/opendesign2api
sudo install -m 0400 -o 10001 -g 10001 \
  "$HOME/.amr/config.json" /var/lib/opendesign2api/amr-config.json
cp .env.example .env
```

Edit `.env` and set both values:

```dotenv
OPENDESIGN2API_CONFIG_FILE=/var/lib/opendesign2api/amr-config.json
OPENDESIGN2API_KEY=replace-with-output-of-openssl-rand-hex-32
```

Then start the service:

```bash
docker compose up -d
docker compose ps
```

Compose binds the host side to `127.0.0.1`, mounts the Vela config read-only at
`/run/secrets/amr-config.json`, and either pulls the GHCR image or builds the same
Dockerfile locally. The image stays on fixed non-root UID/GID `10001:10001`; the
dedicated mode-`0400` copy keeps the original login file untouched and unreadable to
other users. Refresh that copy after `vela login` rotates the local profile.

## Use it

```bash
curl http://127.0.0.1:8792/v1/models \
  -H "Authorization: Bearer $OPENDESIGN2API_KEY"

curl http://127.0.0.1:8792/v1/chat/completions \
  -H "Authorization: Bearer $OPENDESIGN2API_KEY" \
  -H 'content-type: application/json' \
  -d '{
    "model": "deepseek-v4-flash",
    "messages": [{"role": "user", "content": "Reply with exactly: OK"}]
  }'
```

OpenAI Python SDK:

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8792/v1",
    api_key=os.environ["OPENDESIGN2API_KEY"],
)

response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=[{"role": "user", "content": "Reply with exactly: OK"}],
)
print(response.choices[0].message.content)
```

Model availability belongs to the logged-in account and can change. Treat
`/v1/models` as the current catalog; run a real completion before calling a listed
model usable.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `OPENDESIGN2API_HOST` | `127.0.0.1` | Listen address |
| `OPENDESIGN2API_PORT` | `8792` | Listen port |
| `OPENDESIGN2API_KEY` | empty | Local Bearer key required from downstream clients when set |
| `OPENDESIGN2API_CONFIG` | `~/.amr/config.json` | Official Vela profile file |
| `OPENDESIGN2API_PROFILE` | `prod` | Profile key inside the config |
| `OPENDESIGN2API_UPSTREAM_TIMEOUT` | `600` | Upstream timeout in seconds |
| `OPENDESIGN2API_MAX_REQUEST_BYTES` | `8388608` | Maximum request body size |
| `OPENDESIGN2API_CLIENT_TIMEOUT` | `30` | Timeout while reading a downstream request |

Keep the listener on loopback unless you intentionally put an authenticated gateway
or private network in front. If anything beyond the same machine can reach the port,
set `OPENDESIGN2API_KEY` and enforce network access controls as well.

## Deploy behind CLIProxyAPI

This repository includes a hardened systemd unit and a step-by-step CPA integration
guide: [docs/deploy-behind-cliproxyapi.md](docs/deploy-behind-cliproxyapi.md).

The safe topology is:

```text
client ──► CLIProxyAPI ──► 127.0.0.1:8792 ──► OpenDesign Link
```

Register it as one `openai-compatibility` provider. OpenDesign exposes an OpenAI
endpoint here; do not invent a paired `claude-api-key` entry without a separately
verified Claude-compatible endpoint.

## Security model

- The upstream runtime key is read only from the Vela config, is never written by the
  gateway, and is redacted if an upstream error body unexpectedly echoes it.
- The downstream key is a separate local bridge secret. It can be rotated without
  changing the OpenDesign login.
- Request logs contain method, route, model, upstream status, streaming mode, and
  latency. They do not intentionally log prompts or Authorization headers.
- Docker and systemd examples keep the service on loopback and mount credentials
  read-only.
- The gateway limits request bodies but uses a thread per connection. It is intended
  for a trusted local aggregator, not as a standalone public multi-tenant edge.

## Limitations

- Only Models and Chat Completions are implemented.
- `/healthz` proves that the configured profile is readable; it does not spend quota
  or call the upstream. Use `scripts/probe.py` for an end-to-end readiness check.
- Credential refresh is external: OpenDesign Desktop or `vela login` updates the
  profile, and operators must securely refresh any server-side copy.
- Upstream models, quotas, routes, latency, and availability are controlled by
  OpenDesign and its model providers.

## Troubleshooting

- `missing ~/.amr/config.json`: sign in with OpenDesign Desktop or run `vela login`,
  then confirm the selected profile exists.
- upstream `401`: refresh the Vela login. Do not copy a runtime key into source code.
- `CERTIFICATE_VERIFY_FAILED`: the selected Python installation has no usable CA
  bundle. Repair that interpreter's certificate setup or use an OS-managed Python.
  Never work around it by disabling TLS verification.

## Legal

This is an independent open-source gateway and is not affiliated with or endorsed by
OpenDesign or Powerformer, Inc. It only works with credentials you provide.

Review the current [OpenDesign Terms of Service](https://open-design.ai/terms/) before
using the hosted service from a server or sharing access. The software license in this
repository does not grant rights to resell, rent, sublicense, bypass limits on, or
otherwise misuse OpenDesign's hosted service. You are responsible for credential
security, model usage, content, costs, and compliance with upstream terms.

## License

[AGPL-3.0-only](LICENSE). If you modify this gateway and let users interact with that
modified version over a network, the AGPL requires you to offer those users the
corresponding source.
