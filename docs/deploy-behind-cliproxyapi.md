# Deploy behind CLIProxyAPI

This guide keeps OpenDesign credentials inside a loopback-only gateway and gives CPA
only a separate local bridge key.

```text
client ──► CLIProxyAPI ──► opendesign2api ──► OpenDesign Link
           user auth        local bridge key    runtime key
```

The two keys are different. Never paste the OpenDesign runtime key directly into a
public command, issue, log, or repository.

## 1. Preflight

Before changing production, verify:

- the exact host and current CPA instance;
- Python 3.9+ and outbound HTTPS access;
- a free loopback port, default `127.0.0.1:8792`;
- enough disk space for a checkout and a timestamped rollback copy;
- the current CPA config hash, owner, mode, service state, and a known-good baseline
  model request.

Do not infer the active CPA config path from examples. Read the actual systemd unit.
Do not print the production config because it contains provider keys.

## 2. Install the gateway

Use a fixed release tag or commit, not an unversioned working tree:

```bash
git clone https://github.com/AstroQore/opendesign2api.git /opt/opendesign2api
cd /opt/opendesign2api
git checkout <release-tag-or-commit>

useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin opendesign2api
install -d -m 0750 -o root -g opendesign2api /etc/opendesign2api
```

If the service user already exists, do not recreate or alter it blindly.

Create `/etc/opendesign2api/opendesign2api.env` as mode `0640`, owner
`root:opendesign2api`:

```dotenv
OPENDESIGN2API_HOST=127.0.0.1
OPENDESIGN2API_PORT=8792
OPENDESIGN2API_CONFIG=/etc/opendesign2api/amr-config.json
OPENDESIGN2API_PROFILE=prod
OPENDESIGN2API_KEY=<random-local-bridge-key>
OPENDESIGN2API_UPSTREAM_TIMEOUT=600
OPENDESIGN2API_MAX_REQUEST_BYTES=8388608
OPENDESIGN2API_CLIENT_TIMEOUT=30
```

Generate the local bridge key with `openssl rand -hex 32`. This key protects the
loopback hop. It is not the value from `~/.amr/config.json`.

Install the unit:

```bash
install -m 0644 deploy/opendesign2api.service /etc/systemd/system/opendesign2api.service
systemctl daemon-reload
```

## 3. Transfer the Vela profile safely

The official local source is normally:

```text
~/.amr/config.json
```

It contains the runtime key. Do not add it to Git and do not build it into the image.
Transfer it through SSH stdin or another approved secret channel. A safe shape is:

```bash
ssh root@your-server 'umask 077; cat > /etc/opendesign2api/amr-config.json.next' \
  < "$HOME/.amr/config.json"

ssh root@your-server \
  'python3 -c "import json; json.load(open(\"/etc/opendesign2api/amr-config.json.next\"))" && \
   chown root:opendesign2api /etc/opendesign2api/amr-config.json.next && \
   chmod 0640 /etc/opendesign2api/amr-config.json.next && \
   mv /etc/opendesign2api/amr-config.json.next /etc/opendesign2api/amr-config.json'
```

Replace `root@your-server` with your inventory-driven SSH target. Avoid putting the
runtime key in an argument or environment variable visible in a process listing.

When `vela login` refreshes the local profile, repeat the guarded transfer. The
gateway reloads the file after its mtime changes.

## 4. Start and verify the gateway

```bash
systemctl enable --now opendesign2api.service
systemctl is-active opendesign2api.service
ss -ltnp | grep ':8792'
curl -fsS http://127.0.0.1:8792/healthz
```

Then run the repository probe on the server with the local bridge key supplied from a
protected environment:

```bash
set -a
. /etc/opendesign2api/opendesign2api.env
set +a
OPENDESIGN2API_BASE=http://127.0.0.1:8792 \
python3 /opt/opendesign2api/scripts/probe.py
unset OPENDESIGN2API_KEY
```

`/healthz` only checks the profile file. The probe's catalog, JSON completion, and SSE
completion are the runtime proof.

## 5. Add one CPA canary model

Read the live YAML style first. A typical `openai-compatibility` entry is:

```yaml
openai-compatibility:
  - name: OpenDesign
    base-url: http://127.0.0.1:8792/v1
    api-key-entries:
      - api-key: "<same-local-bridge-key>"
    models:
      - name: deepseek-v4-flash
        alias: ""
```

This matches Cursor Direct at the provider layer: CPA stores complete model ids and
the provider entry has no `prefix` field. The gateway is transparent, so the ids stay
exactly as OpenDesign returns them.

This is a single OpenAI-compatible provider. Do not add a Claude pair unless
OpenDesign separately documents and you verify a Claude-compatible endpoint.

Stage the CPA edit as a config transaction:

1. create a timestamped backup and record hash, owner, and mode;
2. generate a candidate without reformatting unrelated YAML or exposing keys;
3. parse the candidate and prove only the intended provider changed;
4. atomically replace the config;
5. restart only the CPA instance whose config changed, if the current runtime requires
   a restart;
6. verify the old baseline first, then the new canary.

Do not restart sibling CPA instances merely because they share the same binary.

## 6. Acceptance

Keep these layers separate:

| Layer | Required proof |
|---|---|
| Gateway config | credential file readable, owner/mode correct |
| Gateway service | unit active, loopback listener, no restart loop |
| Gateway runtime | models + JSON completion + SSE completion pass |
| CPA config | intended provider only, valid YAML, rollback ready |
| CPA catalog | expected prefixed canary appears |
| CPA runtime | real completion through the CPA user endpoint |
| Observability | manager/usage views remain healthy and usage keeps advancing |

A model appearing in `/v1/models` is `listed`, not automatically `runtime_ok`.

## Rollback

Before every gateway upgrade, require a clean checkout and record the exact current
commit in a mode-`0600` maintenance file:

```bash
cd /opt/opendesign2api
test -z "$(git status --porcelain)"
install -d -m 0700 /root/opendesign2api-maintenance
git rev-parse HEAD > /root/opendesign2api-maintenance/previous-commit
chmod 0600 /root/opendesign2api-maintenance/previous-commit
```

If the new gateway fails, restore that exact revision and verify runtime again:

```bash
cd /opt/opendesign2api
git checkout "$(cat /root/opendesign2api-maintenance/previous-commit)"
systemctl restart opendesign2api.service
systemctl is-active opendesign2api.service
```

For container production, pin `OPENDESIGN2API_IMAGE` to a release tag or digest and
record the previous value before updating; do not rely on `latest` as a rollback
handle.

If the CPA baseline regresses, restore the exact config backup, owner, and mode, then
restart only the affected CPA instance and repeat the baseline.

Do not delete credential backups or usage data as part of a routing rollback. Retain
the failed candidate and a sanitized failure record for diagnosis.
