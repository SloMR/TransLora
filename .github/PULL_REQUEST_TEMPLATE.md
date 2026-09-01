## What changed

<!-- One or two sentences. Link the issue if there is one. -->

## Parity

TransLora ships the same translator twice — `cli/core/` and `web/src/app/core/`.

- [ ] This change touches only one side, and that is correct (UI-only or CLI-only), **or**
- [ ] Both sides were changed and still produce the same prompt / block splitting / output.
- [ ] Version bump, if any, is applied everywhere `scripts/check-versions.sh` looks.

## Checks

- [ ] `make lint` and `make typecheck` pass
- [ ] `make test-cli` / `make test-web` pass for the side(s) touched
- [ ] No API key, endpoint, or subtitle content added to a log line, fixture, or test
