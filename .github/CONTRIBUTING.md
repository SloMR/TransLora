# Contributing

## The one rule that is easy to get wrong

TransLora ships the **same translator twice**: `cli/core/` (Python) and
`web/src/app/core/` (TypeScript). Prompt text, batch splitting, block
validation, context-pass parsing and output formatting must stay behaviourally
identical. `cli/tests/test_parity.py` checks some of this, but not all — if you
change one side, change the other in the same PR or say why it is one-sided.

## Setup

```sh
nvm use          # Node 20, per .nvmrc
make install     # npm ci in web/, uv sync in cli/
```

You need [uv](https://docs.astral.sh/uv/) (it also fetches the pinned ruff and
mypy on demand) and Docker only for the `docker-*` targets.

## The commands

`make` lists them. CI runs exactly these targets, so a green `make lint test`
locally means a green CI.

| Target | What |
| --- | --- |
| `make lint` | ruff (cli), eslint (web), shellcheck (scripts) |
| `make typecheck` | mypy (cli), tsc (web app sources) |
| `make test-cli` / `make test-web` | pytest / Karma with coverage |
| `make build-web` | production build — this is what enforces the bundle budgets |
| `make docker` | build both images |
| `make release-dry` | render release notes for the latest tags, publish nothing |

`npm run format` (in `web/`) applies every fix ESLint can make on its own.
**There is no Prettier, on purpose:** `web/.editorconfig` scopes
`quote_type = single` to `[*.ts]`, so Prettier rewrites the TypeScript
expressions *inside Angular templates* to double quotes and reformats every
source file.

## Commit messages

`scripts/create-releases.sh` builds the changelog from them, so the prefix
decides which release a commit lands in:

- `Web:` / `CLI:` — goes to that component only.
- `CI:`, `Docs:`, `Script:` — shared; routed by keywords in the description, and
  listed in *both* releases when it hints at both components or at neither.
- The section (Features / Fixes / Performance / …) comes from a conventional
  type (`feat:`, `fix:`) or from keywords in the description.

Check with `make release-dry` before tagging.

## Releasing

1. Bump the version everywhere `scripts/check-versions.sh` looks:
   `web/package.json` + `web/package-lock.json`, or `cli/pyproject.toml` +
   `cli/translora.py`.
2. `make versions`.
3. Tag `web/x.y.z` or `cli/x.y.z` and push it. `.github/workflows/release.yml`
   re-checks the tag against the version files and publishes the release.
