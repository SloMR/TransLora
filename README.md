<h1 align="center">
  <img src="web/public/favicon.svg" alt="TransLora" width="56" height="56"><br>
  TransLora
</h1>

<p align="center">
  <em>AI-powered subtitle translator with batched LLM calls and block-level validation.</em>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
  <a href="https://angular.dev"><img alt="Angular 22" src="https://img.shields.io/badge/Angular-22-DD0031?logo=angular&logoColor=white"></a>
  <a href="https://www.python.org/"><img alt="Python 3.12+" src="https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white"></a>
  <a href="https://www.typescriptlang.org/"><img alt="TypeScript" src="https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript&logoColor=white"></a>
  <img alt="Platform: Web · CLI" src="https://img.shields.io/badge/platform-Web%20%C2%B7%20CLI-lightgrey">
</p>

---

Works with any OpenAI-compatible endpoint — local servers, OpenAI, Groq, DeepSeek, OpenRouter, and more.

Two interfaces share the same pipeline:

- **Web app** — an Angular 22 single-page app, pure browser, no backend required.
- **CLI** — a small Python 3.12+ tool for scripting and bulk jobs.

## Highlights

- **Batched translation** — sends ~10 subtitle blocks at a time so small models don't drift, skip short lines, or merge split sentences.
- **Cast & register prepass** — a pre-scan extracts characters, recurring terms, and the written register; each batch gets the slice matching its own names and terms, so spelling and formality stay consistent. Export it with `--glossary-out` to reuse across a season.
- **Strict validation** — every batch is checked for block count, the model's own numbering, unchanged timestamps, blanked lines, and timestamp lines leaking into the text; failures retry with back-off and recursively split on repeated failure.
- **Deterministic repairs** — dropped formatting tags and speaker dashes restored, subtitles re-wrapped to the source's line count and the script's line length, RTL punctuation and sentence-final marks fixed, all without an API call. What can't be repaired locally — text bleeding in from the next line, glossary drift, foreign-script leakage — is flagged, one capped retry re-translates those batches with the problem named, and any line still flagged after that is re-translated on its own.
- **Split providers** — keep bulk translation on a cheap or local model and send only the review pass to a stronger one.
- **Auto-detect source language** — omit the source and the model infers it from the text, so mixed-language batches translate to a single target cleanly.
- **Any OpenAI-compatible provider** — local or cloud, no vendor lock-in.
- **Parallelism** — translate many batches per file and many files at once.
- **Live progress** — per-file progress bars in the web app, an in-place status line (elapsed / ETA / throughput) in the CLI.

## Web app

```bash
cd web
npm install
npm start
```

Open http://localhost:4200, drop in one or more subtitle files, pick a target language (source defaults to Auto-detect) and a provider, and download translated files individually or as a ZIP. Your settings are remembered between visits; neither API key ever is.

**If every batch fails instantly, it's CORS.** The browser calls the provider directly, so the provider must send `Access-Control-Allow-Origin`. OpenAI, Groq, DeepSeek and OpenRouter do; a local server usually needs telling — `llama-server --api-key none` accepts cross-origin requests, LM Studio has a CORS toggle in its server settings. A CORS rejection reaches JavaScript as a status-0 error with no body, so the app names it rather than reporting "HTTP 0".

## Command line

```bash
# Install the command once (any OS; needs Python 3.12+)
uv tool install ./cli          # or: pipx install ./cli
translora
```

`translora` on its own opens a guided session: arrow-key menus for the target language, an optional dialect, the provider and model (cheapest first, with prices), the key (hidden, or taken from `TRANSLORA_API_KEY`), the quality, an options menu (files at once, requests in flight, register, overwriting), and the file or folder. Every question can be stepped back from with the ⌫ key or the left arrow. Then it runs, and offers to translate more files with the same answers, overwrite the last ones, or change a setting. The answers are kept in the platform's own settings folder for next time — never the key — and offered at the next start, not pre-chosen. Quitting prints the command line that would do the same in a script.

```
? Translate into which language?   Arabic
? Source language                  Auto-detect
? Which provider?                  OpenAI · gpt-5.6-luna · $0.20 in, $1.20 out per 1M tokens
? Which OpenAI model?              gpt-5.6-luna · cheapest
? OpenAI API key                   ********
? Quality                          Best · Everything on: every scene attributed, and a back-translation check on a sample.
? Subtitle file or folder          ~/subs/season4
```

Flags are for scripts. Three of them cover nearly every run, and `-q` bundles the quality knobs the way the session and the web app do:

```bash
translora movie.srt -t Arabic --provider openai -q best
translora ./season/ -t Arabic --api-url http://127.0.0.1:8080/v1/chat/completions --model qwen3 -q fast

# From a checkout without installing
cd cli && uv sync && uv run translora.py movie.srt -t Arabic --provider groq
```

While a file runs, one status line covers every step — reading the file, translating, checking, repairing — with the percent for the whole run and an ETA. When it is done, the run says what it flagged, how much it fixed on its own, and lists the lines it could not, by number.

```bash
cd cli

# Explicit source language (skip auto-detect)
translora movie.srt -s English -t Arabic \
  --api-url http://127.0.0.1:8080/v1/chat/completions

# Cloud provider, whole folder in parallel (source auto-detected per file)
translora ./subs/ -t Arabic --provider openai -c 10 -pf 3
```

Frequently used flags:

| Flag | Description |
| --- | --- |
| `-t, --target` | Target language name (required) |
| `-s, --source` | Source language (optional; omit to auto-detect — useful for mixed-language batches) |
| `--api-url` | OpenAI-compatible `/v1/chat/completions` endpoint |
| `--api-key` | API key; use `none` for local servers |
| `--model` | Model name (optional for local) |
| `--review-api-url` | Send only the review pass to a different endpoint (default: `--api-url`); no extra calls, the review calls just go elsewhere. |
| `--review-api-key` | Key for `--review-api-url` (default `$TRANSLORA_REVIEW_API_KEY`, else `--api-key`). |
| `--review-model` | Model for the review pass (default `--model`). |
| `--batch-size` | Subtitle blocks per batch (default **10**) |
| `-c, --concurrency` | Parallel batches per file (default **1**); match it to your server's slot count — see [Speed](#speed). |
| `-pf, --parallel-files` | Files translated in parallel (default **1**) |
| `--max-retries` | Attempts per batch on HTTP/network failures (default **5**); validation failures split the batch after two instead. |
| `--timeout` | Per-request timeout in seconds (default **120**) — raise it for local CPU inference. |
| `--encoding` | Source encoding (default **auto**: UTF-8, then cp1252, cp1256, cp1251, shift_jis); name a codec when detection gets it wrong. |
| `--dry-run` | Print the planned work and LLM call count, then exit without calling the API. |
| `--glossary-out` | Write the scanned glossary (cast, terms, register, scenes) to JSON; single file only. |
| `--glossary-in` | Load a glossary from JSON instead of scanning, saving the scan call per file. |
| `--no-resume` | Don't reuse or write the `<output>.translora-progress.json` checkpoint. |
| `--force` | Re-translate even if the output exists |
| `-v, --verbose` | Show retry/validation warnings (hidden by default) |
| `-o, --output` | Output path (single file only) |
| `--scan-budget` | Chars sent to the prepass scan (default **24000**). Lower on tight-context local models (~8k window); raise on large-context cloud models for full-file scans. |
| `--context-overlap` | Previous-batch source blocks shown as read-only context (default **2**, `0` to disable). Helps speaker continuity across batch boundaries. |
| `--no-review` | Disable the post-edit review pass; saves one call per batch whose glossary slice names a character or term. |
| `--no-refine-attribution` | Disable per-block speaker attribution for mixed-gender scenes (saves one small call per ambiguous scene). |
| `--full-attribution` | Attribute speakers in every scene that has a cast, not only mixed-gender ones (adds one small call per extra scene). |
| `--no-fix-flagged` | Disable the focused retry of batches flagged for dropped tags, speaker dashes, glossary drift, cross-line bleeding or foreign-script leakage (the retry costs one call per flagged batch, capped at 5% of the file, minimum 2; lines still flagged afterwards are re-issued alone, under the same 5% cap of the file's lines). |
| `--verify-adequacy` | Back-translate a fifth of the batches (minimum 2) and flag lines that lost meaning so the retry above fixes them; needs `-s/--source` and adds ~20% more calls. |
| `--formality` | Address the viewer `formal`ly or `informal`ly (default **auto** — follow the source's own register). |
| `--dialect` | Target variant, e.g. `"Saudi Arabic"`, `"Brazilian Portuguese"`. Also replaces the scanned register guess in the prepass. |
| `--max-line-chars` | Override the target script's line length (default **42** for Latin/Cyrillic/Arabic, **20** for Korean, **16** for Chinese/Japanese — see `--dry-run`). |
| `--no-reflow` | Don't re-wrap translated subtitles to the source's line count and the script's line length (local, no extra API calls). |

The defaults are tuned for best translation quality. On metered cloud providers you can pass `--no-review`, `--no-refine-attribution` and/or `--no-fix-flagged` to cut LLM calls; `--verify-adequacy` and `--full-attribution` go the other way and are off by default because they cost calls. On tight-context local models, lower `--scan-budget` (e.g. `8000`) so the scan prompt fits.

Set `NO_COLOR=1` to disable ANSI colors; output auto-falls back to plain lines when piped. Set `TRANSLORA_API_KEY` to keep the key out of your shell history and the process table — `--api-key` still wins when both are present. `TRANSLORA_REVIEW_API_KEY` does the same for `--review-api-key`.

Exit codes: **0** every file translated or skipped, **1** at least one file failed or the arguments were invalid, **130** interrupted with Ctrl-C.

### Speed

**Match `--concurrency` to the number of slots your server actually has.** Micro-benchmarks lie here. On an Apple Silicon machine, 6 tiny requests ran **4.5x faster** in parallel than one after another — but the same server, given a real 372-line file, was **slower** at `-c 6` (**10m25s**) than at `-c 1` (**9m03s**). Real batches carry a system prompt, a glossary slice and the previous batch's tail, so six of them oversubscribe a 2-slot server and the slots keep evicting each other's prefix cache; the cache hits you lose cost more than the parallelism you gain.

So:

- **Local**: set `-c` to the server's slot count — `-np` in `llama.cpp` (`llama-server -np 4` → `-c 4`). Fewer is safe; more is usually a loss.
- **Watch the context split.** `llama.cpp`'s own `-c` is the *total* context divided across slots: `-c 8192 -np 4` leaves each slot 2048 tokens, which the prepass scan will not fit in. Raise the server's `-c` with `-np`, or lower `--scan-budget`.
- **Cloud**: there are no slots to oversubscribe — raise `-c` (10 is fine) and add `-pf` for whole folders.

The `--dry-run` estimate assumes every lane stays busy, so it tracks reality only when the lanes match the slots. The web app's **Concurrency** field is the same knob; it defaults to 5 because it is aimed at cloud providers.

### A stronger model for the review pass

Quality is model-bound, and the review pass is where a bigger model pays off most: it sees the source, the first-pass translation and the glossary, and only fixes what is wrong. `--review-api-url`, `--review-api-key` and `--review-model` send that one pass somewhere else — bulk translation stays on the cheap or local model, review goes to the strong one. Each field falls back to the main provider's, so overriding the model alone is enough when both live at the same endpoint.

```bash
# Local model does the translating; a cloud model reviews.
translora movie.srt -s English -t Arabic \
  --api-url http://127.0.0.1:8080/v1/chat/completions \
  --review-api-url https://api.openai.com/v1/chat/completions \
  --review-api-key sk-... --review-model gpt-5.6-sol
```

`--dry-run` prints both endpoints (and whether the review one has its own key) so you can check the routing before spending anything. The web app has the same three fields under **Advanced**.

### Dry run

```bash
translora ./season/ -t Arabic --api-url ... --dry-run
```

Prints the endpoints each pass will call, the line norms for the target, per-file block/batch/call counts and a wall-clock estimate, then exits without touching the API.

### Legacy encodings

Subtitle files are often windows-1256 (Arabic), cp1251 (Cyrillic) or latin-1 rather than UTF-8. Detection is automatic and the encoding used is printed per file; if a file decodes to nonsense, name the codec — `--encoding cp1256`. Output is always UTF-8.

### Resuming an interrupted run

Completed batches are checkpointed to `<output>.translora-progress.json` as they land, so a crash, a rate limit or a Ctrl-C doesn't discard a file's paid work — re-run the same command to pick up where it stopped. The checkpoint is keyed to the input, target language, model and batch size, so changing any of them starts fresh; it is deleted on success. `--no-resume` opts out.

### Reusing one glossary across a series

The scan derives cast and register per file, so two episodes can spell a character's name differently. Scan once, then reuse it:

```bash
translora ep01.srt -t Arabic --api-url ... --glossary-out cast.json
translora ./season/ -t Arabic --api-url ... --glossary-in cast.json
```

`cast.json` is plain JSON — fix a wrong name or register by hand and re-run.

## Docker

Both interfaces ship with a `Dockerfile` so you can build and run without installing Node, Angular CLI, Python, or any deps locally.

### Web app

```bash
# from the repo root
docker build -t translora-web ./web
docker run --rm -p 8080:8080 translora-web
```

Open http://localhost:8080. A small `nginxinc/nginx-unprivileged:alpine` serves the production Angular build with SPA-fallback routing, gzip and security headers; it runs non-root on 8080, so no privileged port is needed.

### CLI

**Step 1 — build the image (one time):**

```bash
# from the repo root
docker build -t translora-cli ./cli
```

**Step 2 — translate a file from your disk.**

The image has no idea what's on your computer. To give it access to your subtitle files, you **mount a folder** from your disk into the container with `-v <host-folder>:/work`. Inside the container that folder appears as `/work`, and the CLI runs from there. Anything written to `/work` is written to your real folder — including the translated output.

Picture it like this:

```
your computer                            inside the container
──────────────────────────────           ──────────────────────────────
C:\Users\you\subs\movie.srt    ◀───────▶  /work/movie.srt
C:\Users\you\subs\movie.ar.srt ◀───────▶  /work/movie.ar.srt   (output)
                          │
                          └── -v "C:\Users\you\subs:/work"
```

So the workflow is: `cd` into the folder containing your subtitle files, then run the container with `-v "$(pwd):/work"`. Pass file names exactly like you would to the local CLI — they resolve relative to `/work` automatically.

**Cloud provider example (OpenAI, OpenRouter, Groq, …):**

```bash
cd /path/to/your/subtitles    # the folder where movie.srt lives

docker run --rm -v "$(pwd):/work" translora-cli movie.srt -t Arabic \
  --api-url https://api.openai.com/v1/chat/completions \
  --api-key sk-... --model gpt-5.6-luna
```

After this finishes, `movie.ar.srt` appears in the same folder on your disk. You can also pass a folder name to translate everything in it (`docker run ... translora-cli ./ -t Arabic ...`).

**Path syntax cheat sheet for the `-v` flag:**

| Shell | Use |
|---|---|
| Linux / macOS / Git Bash | `-v "$(pwd):/work"` |
| Windows PowerShell | `-v "${PWD}:/work"` |
| Windows cmd.exe | `-v "%cd%:/work"` |

You can also pass an absolute path explicitly: `-v "C:\Users\you\subs:/work"` (Windows) or `-v "/home/you/subs:/work"` (Linux).

**Local LLM server on your host machine.**

If you're running an LLM server on your own computer (e.g. on `http://127.0.0.1:8080`), `127.0.0.1` from inside the container points at the container itself, not your host. Use `host.docker.internal` instead. On Linux you also need `--add-host=host.docker.internal:host-gateway`:

```bash
docker run --rm -v "$(pwd):/work" \
  --add-host=host.docker.internal:host-gateway \
  translora-cli movie.srt -t Arabic \
  --api-url http://host.docker.internal:8080/v1/chat/completions
```

(`--add-host` is harmless on Mac and Windows where Docker Desktop maps `host.docker.internal` automatically — leave it in for cross-platform copy/paste.)

### Notes

- `--rm` deletes the container after it exits so they don't pile up. Drop it if you want to keep the container around for debugging.
- Both Dockerfiles use BuildKit cache mounts for `npm` and `pip`, so re-builds after a small code change finish in a few seconds.

## How it works

Small and medium LLMs have known failure modes on long subtitle files: skipping one-word blocks (`"Oh!"`, `"Hmm."`), merging sentences split across two blocks for timing, drifting mid-file, and switching dialect or formality between batches. TransLora defends against that with a nine-step pipeline:

1. **Parse** the file into numbered blocks with timestamps (SRT, VTT, ASS, SSA, SBV, SUB). Every format normalises to one internal shape and is written back through its own writer, so styles and headers survive.
2. **Scan** — one LLM call over the file (stride-sampled to `--scan-budget`) extracts the cast, recurring terms, and the written register (e.g. Modern Standard Arabic, peninsular Spanish, polite Japanese).
3. **Attribute** — one small LLM call per multi-speaker scene labels who says each line, so second-person forms get the addressee's gender right. Skipped for scenes that can't be ambiguous; `--full-attribution` runs it everywhere, `--no-refine-attribution` not at all.
4. **Batch** — blocks are split into groups small enough that the model can't drift, and sent with a structure-preserving system prompt plus the glossary slice matching that batch's characters and terms.
5. **Validate** — block count in = out, the model's own block numbers unchanged, timestamps untouched, no block blanked, no timestamp line leaked into the text. Two validation failures split the batch in half and recurse down to singletons.
6. **Review** — batches whose glossary slice names a character or term get one conservative second pass that fixes gender, name and agreement slips. Disable with `--no-review`, or send it to a stronger model with `--review-model`.
7. **Repair, without asking the model** — the text that will ship gets its dropped formatting tags and speaker dashes restored, is re-wrapped to the source's line count and the script's line length, and has its RTL punctuation and sentence-final mark fixed. It is then read for what no local fix can solve: text bleeding in from the next line, glossary terms rendered inconsistently, and foreign-script leakage (Latin or Han left sitting in an Arabic line). No API calls; `--no-reflow` opts out of the re-wrap.
8. **Fix what was flagged** — the batches step 7 flagged are re-translated once with the specific problem named, capped at 5% of the file, and the retry is kept only if it comes back with fewer flags. Any line still flagged afterwards is then re-translated on its own, its problems named — a correction a model lets slide inside a ten-line batch it usually follows when the line is all there is to do — under the same acceptance rule and a 5% cap of the file's lines. `--verify-adequacy` adds a back-translation spot check first, so lines that quietly lost meaning join that queue. Disable with `--no-fix-flagged`.
9. **Stitch and write** — validated batches are reassembled in order, one last file-wide pass strips vocalisation from the few Arabic lines that came back carrying it when the rest of the file does not, and the result is rebuilt into the original format. Transport failures (429, 5xx, timeouts) retry up to `--max-retries` with backoff that honours `Retry-After`; completed batches are checkpointed so a failure never discards the whole file.

**What a run costs.** A 900-block episode at the defaults: 1 scan call, one per qualifying scene (typically 20–40), 90 translate calls, one review call per batch whose glossary slice hits a character or term, and up to 5 repair calls — roughly **150–200 calls**, not 90. Each finished file prints its own breakdown by pass, and `--dry-run` prints the projection before you spend anything; `--no-review` roughly halves it, `--verify-adequacy` adds 18 more.

## Providers

TransLora works with **any OpenAI-compatible `/v1/chat/completions` endpoint** — there is no fixed provider list and no vendor lock-in. Pick the **Custom** option in the web app (or pass `--api-url` in the CLI) and point it at whatever URL you like: a hosted service, a self-hosted server, or a model running on your own machine.

For convenience, the table below lists a few known-working endpoints you can paste in directly:

| Example | Endpoint |
| --- | --- |
| Local OpenAI-compatible server | `http://127.0.0.1:8080/v1/chat/completions` |
| OpenAI | `https://api.openai.com/v1/chat/completions` |
| Groq | `https://api.groq.com/openai/v1/chat/completions` |
| DeepSeek | `https://api.deepseek.com/v1/chat/completions` |
| OpenRouter | `https://openrouter.ai/api/v1/chat/completions` |

Anything else that speaks the OpenAI chat-completions protocol will work the same way — just provide the URL, an API key (or `none` for keyless local servers), and a model name.

## Repository layout

```
.
├── web/        Angular 22 app (primary interface)
│   ├── src/app/        UI: file intake, provider form, advanced panel, run results
│   └── src/app/core/   Parsers, prompts, glossary prepass, repairs, batching, HTTP
├── cli/        Python 3.12+ CLI
│   ├── translora.py    Entry point
│   └── core/           The same pipeline, plus resume checkpoints and the live terminal UI
├── scripts/    Release-notes generator and version checks
└── Makefile    Every command CI runs (make help)
```

## Requirements

- **Web**: Node 24 (see `.nvmrc`); the Angular CLI comes from `npm install`, no global install needed
- **CLI**: Python 3.12+ with `httpx` and `pysubs2` (both in `requirements.txt` / `pyproject.toml`)
- An OpenAI-compatible LLM endpoint (local or hosted)

## Roadmap

- Side-by-side preview and per-block editing in the web app
- General document/text translation beyond subtitles

## License

MIT — see [LICENSE](LICENSE).
