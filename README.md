<h1 align="center">
  <img src="web/public/favicon.svg" alt="TransLora" width="56" height="56"><br>
  TransLora
</h1>

<p align="center">
  <em>AI-powered subtitle translator with batched LLM calls and block-level validation.</em>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
  <a href="https://angular.dev"><img alt="Angular 19" src="https://img.shields.io/badge/Angular-19-DD0031?logo=angular&logoColor=white"></a>
  <a href="https://www.python.org/"><img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white"></a>
  <a href="https://www.typescriptlang.org/"><img alt="TypeScript" src="https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript&logoColor=white"></a>
  <img alt="Platform: Web · CLI" src="https://img.shields.io/badge/platform-Web%20%C2%B7%20CLI-lightgrey">
</p>

---

Works with any OpenAI-compatible endpoint — local servers, OpenAI, Groq, DeepSeek, OpenRouter, and more.

Two interfaces share the same pipeline:

- **Web app** — an Angular 19 single-page app, pure browser, no backend required.
- **CLI** — a small Python 3.10+ tool for scripting and bulk jobs.

## Highlights

- **Batched translation** — sends ~10 subtitle blocks at a time so small models don't drift, skip short lines, or merge split sentences.
- **Cast & register prepass** — a pre-scan extracts characters, recurring terms, and the written register so every batch translates names and formality consistently.
- **Strict validation** — every batch is checked for block count, numbering, and unchanged timestamps; failures retry with back-off and recursively split on repeated failure.
- **Auto-detect source language** — omit the source and the model infers it from the text, so mixed-language batches translate to a single target cleanly.
- **Any OpenAI-compatible provider** — local or cloud, no vendor lock-in.
- **Parallelism** — translate many batches per file and many files at once.
- **Live progress** — per-file progress bars in the web app, an in-place status line (elapsed / ETA / throughput) in the CLI.

## Web app

```bash
cd web
npm install
ng serve
```

Open http://localhost:4200, drop in one or more subtitle files, pick a target language (source defaults to Auto-detect) and a provider, and download translated files individually or as a ZIP.

## Command line

```bash
cd cli

# Option A — pip
pip install -r requirements.txt
python translora.py movie.srt -t Arabic \
  --api-url http://127.0.0.1:8080/v1/chat/completions

# Option B — uv (faster, auto-manages the venv)
uv sync
uv run translora.py movie.srt -t Arabic \
  --api-url http://127.0.0.1:8080/v1/chat/completions

# Explicit source language (skip auto-detect)
python translora.py movie.srt -s English -t Arabic \
  --api-url http://127.0.0.1:8080/v1/chat/completions

# Cloud provider, whole folder in parallel (source auto-detected per file)
python translora.py ./subs/ -t Arabic \
  --api-url https://api.openai.com/v1/chat/completions \
  --api-key sk-... --model gpt-4.1-mini -c 10 -pf 3
```

Frequently used flags:

| Flag | Description |
| --- | --- |
| `-t, --target` | Target language name (required) |
| `-s, --source` | Source language (optional; omit to auto-detect — useful for mixed-language batches) |
| `--api-url` | OpenAI-compatible `/v1/chat/completions` endpoint |
| `--api-key` | API key; use `none` for local servers |
| `--model` | Model name (optional for local) |
| `--batch-size` | Subtitle blocks per batch (default **10**) |
| `-c, --concurrency` | Parallel batches per file (default **1** — raise for cloud providers) |
| `-pf, --parallel-files` | Files translated in parallel (default **1**) |
| `--max-retries` | Retries per batch (default **5**) |
| `--force` | Re-translate even if the output exists |
| `-v, --verbose` | Show retry/validation warnings (hidden by default) |
| `-o, --output` | Output path (single file only) |
| `--scan-budget` | Chars sent to the prepass scan (default **24000**). Lower on tight-context local models (~8k window); raise on large-context cloud models for full-file scans. |
| `--context-overlap` | Previous-batch source blocks shown as read-only context (default **2**, `0` to disable). Helps speaker continuity across batch boundaries. |
| `--no-review` | Disable the post-edit review pass. Saves one extra LLM call per batch — useful on metered providers. |
| `--no-refine-attribution` | Disable per-block speaker attribution for mixed-gender scenes (saves one small call per ambiguous scene). |

The defaults are tuned for best translation quality. On metered cloud providers you can pass `--no-review` and/or `--no-refine-attribution` to cut LLM calls. On tight-context local models, lower `--scan-budget` (e.g. `8000`) so the scan prompt fits.

Set `NO_COLOR=1` to disable ANSI colors; output auto-falls back to plain lines when piped.

## Docker

Both interfaces ship with a `Dockerfile` so you can build and run without installing Node, Angular CLI, Python, or any deps locally.

### Web app

```bash
# from the repo root
docker build -t translora-web ./web
docker run --rm -p 8080:80 translora-web
```

Open http://localhost:8080. The image is a small `nginx:alpine` serving the production Angular build, with SPA-fallback routing pre-configured.

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
  --api-key sk-... --model gpt-4.1-mini
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

Small and medium LLMs have known failure modes on long subtitle files: skipping one-word blocks (`"Oh!"`, `"Hmm."`), merging sentences split across two blocks for timing, drifting mid-file, and switching dialect or formality between batches. TransLora defends against that with a six-step pipeline:

1. Parse the subtitle file into numbered blocks with timestamps (SRT, VTT, ASS, SSA, SBV, SUB).
2. Pre-scan the file with one extra LLM call to extract the cast, recurring terms, and the written register (e.g. Modern Standard Arabic, peninsular Spanish, polite Japanese). The relevant slice is attached to each batch so names and formality stay consistent across the whole file.
3. Split blocks into batches small enough that the model can't drift.
4. Send each batch with a structure-preserving system prompt.
5. Validate the response: block count in = out, numbers and timestamps untouched. Repeated failures recursively split the batch down to singletons before giving up.
6. Retry failed batches up to `--max-retries` before flagging the file, then stitch the validated batches back in order.

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
├── web/        Angular 19 app (primary interface)
│   └── src/app/core/   Subtitle parsers, prompt, languages, providers, time tracker, HTTP service
├── cli/        Python 3.10+ CLI
│   ├── translora.py    Entry point
│   └── core/           Batching, HTTP, retries, ETA, live terminal UI
├── DESIGN.md   Visual-design notes for the web app
└── CLAUDE.md   Architecture overview and the full translation prompt
```

## Requirements

- **Web**: Node 18+ and Angular CLI 19
- **CLI**: Python 3.10+ and `httpx`
- An OpenAI-compatible LLM endpoint (local or hosted)

## Roadmap

- Side-by-side preview and per-block editing in the web app
- General document/text translation beyond subtitles

## License

MIT — see [LICENSE](LICENSE).
