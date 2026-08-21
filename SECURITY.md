# Security policy

## Supported versions

Security fixes are applied to the latest release and the default branch.

## Report a vulnerability

Please use GitHub's private vulnerability reporting for this repository. Do not open a
public issue containing credentials, request bodies, account identifiers, private host
details, or exploit instructions against a live service.

If a runtime key or local bridge key may have leaked, rotate or revoke it first. A bug
report cannot make a compromised credential safe again.

## Credential boundary

No real `~/.amr/config.json`, `auth.json`, `.env`, runtime key, cookie, or token belongs
in this repository. Tests must use synthetic values. Container images must receive
credentials only at runtime through a read-only mount or a secret manager.
