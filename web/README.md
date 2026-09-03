# TransLora web app

The browser interface: an Angular 22 single-page app that parses subtitle files,
calls an OpenAI-compatible LLM endpoint directly from the browser, and writes the
translation back in the original format. No backend — nothing leaves the machine
except the chat requests to the provider you choose.

See the [root README](../README.md) for what the pipeline does and which providers work.

## Develop

```bash
npm install
npm start          # http://localhost:4200
```

## Checks

```bash
npm run lint       # eslint (angular-eslint + typescript-eslint)
npm test           # Vitest, watch mode
npm run build      # production build; enforces the bundle budgets
```

From the repo root, `make test-web`, `make lint-web`, `make typecheck-web` and
`make build-web` run exactly what CI runs (headless Chrome, coverage on).

## Layout

| Path | What lives there |
| --- | --- |
| `src/app/app.component.*` | The page shell: the workspace layout, which phase the stage is in, and the wiring between the panels |
| `src/app/file-intake/`, `provider-form/`, `advanced-panel/`, `run-results/` | The panels: the files rail, settings, the advanced popover, and the run summary: what the run repaired and which lines it could not |
| `src/app/run-settings.ts`, `run-presets.ts` | Every knob: its clamp, its default, its `localStorage` round-trip — and the Fast/Balanced/Best bundles of the quality ones |
| `src/app/theme.ts` | Light/dark, resolved against the OS setting |
| `src/app/core/translation-runner.service.ts` | The multi-file queue: worker pool, progress, cancellation, retry |
| `src/app/core/translation.service.ts` | Per-file orchestration: prepass, batch pool, flags, the capped repair pass |
| `src/app/core/batch-runner.ts` | One batch: the two retry budgets, recursive split, review pass, deterministic repair |
| `src/app/core/chat-client.ts` | One chat call: credential hygiene, timeout, retryable errors |
| `src/app/core/context-pass.ts` | The prepass glossary: the model, per-batch slicing, drift detection |
| `src/app/core/context-scan.ts`, `context-parse.ts` | The scan and attribution calls, and parsing what they return |
| `src/app/core/srt-parser.ts` | Wire format and block validation |
| `src/app/core/repair.ts` | Deterministic repairs: tags, speaker dashes, reflow, punctuation, script leaks |
| `src/app/core/testdata/` | The synthetic aligned run the whole-file repair tests measure against |
| `src/app/core/adequacy.ts` | Opt-in back-translation spot check of a sample of batches |
| `src/app/core/run-stats.ts` | LLM calls per pass: the pre-run estimate and the run summary |
| `src/app/core/subtitle-formats/` | Per-format parse/rebuild (SRT, VTT, ASS/SSA, SBV, MicroDVD) |
| `src/styles/` | Shared control primitives, imported by `src/styles.scss` |

`src/app/core/` mirrors `cli/core/`; prompts and tuning constants must stay
identical across both trees — `cli/tests/test_parity.py` fails the build if they drift.

## Docker

```bash
docker build -t translora-web .
docker run --rm -p 8080:8080 translora-web
```

Serves the production build from `nginxinc/nginx-unprivileged` as a non-root user.

## Concurrency

Concurrency defaults to **5**, which suits cloud providers. Against a local
server, set it to that server's slot count instead (`-np` in `llama.cpp`) —
more in-flight batches than slots makes a real file *slower*, not faster. The
root README's [Speed](../README.md#speed) section has the measurements.
