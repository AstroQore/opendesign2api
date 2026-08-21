# Contributing

## Development

The runtime has no third-party Python dependency.

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q src scripts tests
```

Use `scripts/probe.py` only with your own OpenDesign login and keep real responses out
of fixtures and commits.

## Pull requests

- Keep the gateway small and OpenAI-compatible.
- Add tests for behavior changes.
- Document new environment variables in both READMEs.
- Do not commit credentials or provider response bodies containing user data.
- Keep Models, Chat Completions, Responses, Claude Messages, and media endpoints
  explicit; do not claim compatibility that has not been tested end to end.
