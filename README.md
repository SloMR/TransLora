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

Works with any OpenAI-compatible endpoint — local llama.cpp servers, OpenAI, Groq, DeepSeek, OpenRouter, and more.

Two interfaces share the same pipeline:

- **Web app** — an Angular 19 single-page app, pure browser, no backend required.
- **CLI** — a small Python 3.10+ tool for scripting and bulk jobs.

## Highlights

- **Batched translation** — sends ~15 subtitle blocks at a time so small models don't drift, skip short lines, or merge split sentences.
- **Strict validation** — every batch is checked for block count, numbering, and unchanged timestamps; failures retry with back-off.
- **Any OpenAI-compatible provider** — local or cloud, no vendor lock-in.
- **Parallelism** — translate many batches per file and many files at once.
- **Live progress** — per-file progress bars in the web app, an in-place status line (elapsed / ETA / throughput) in the CLI.

## Web app

```bash
cd web
npm install
ng serve
```

Open http://localhost:4200, drop in one or more subtitle files, pick source/target languages and a provider, and download translated files individually or as a ZIP.

## Command line

```bash
cd cli
pip install -r requirements.txt

# Local llama-server (no key needed)
python translora.py movie.srt -s English -t Arabic \
  --api-url http://127.0.0.1:8080/v1/chat/completions

# Cloud provider, whole folder in parallel
python translora.py ./subs/ -s English -t Arabic \
  --api-url https://api.openai.com/v1/chat/completions \
  --api-key sk-... --model gpt-4.1-mini -c 10 -pf 3
```

Frequently used flags:

| Flag | Description |
| --- | --- |
| `-s, --source` / `-t, --target` | Source and target language names |
| `--api-url` | OpenAI-compatible `/v1/chat/completions` endpoint |
| `--api-key` | API key; use `none` for local servers |
| `--model` | Model name (optional for local) |
| `--batch-size` | Subtitle blocks per batch (default **15**) |
| `-c, --concurrency` | Parallel batches per file (default **1**) |
| `-pf, --parallel-files` | Files translated in parallel (default **1**) |
| `--max-retries` | Retries per batch (default **5**) |
| `--force` | Re-translate even if the output exists |
| `-o, --output` | Output path (single file only) |

Set `NO_COLOR=1` to disable ANSI colors; output auto-falls back to plain lines when piped.

## How it works

Small and medium LLMs have known failure modes on long subtitle files: skipping one-word blocks (`"Oh!"`, `"Hmm."`), merging sentences split across two blocks for timing, and drifting mid-file. TransLora defends against that with a five-step pipeline:

1. Parse the `.srt` into numbered blocks with timestamps.
2. Split blocks into batches small enough that the model can't drift.
3. Send each batch with a structure-preserving system prompt.
4. Validate the response: block count in = out, numbers and timestamps untouched.
5. Retry failed batches up to `--max-retries` before flagging the file, then stitch the validated batches back in order.

## Supported providers

Any OpenAI-compatible `/v1/chat/completions` endpoint. Tested targets:

| Provider | Endpoint |
| --- | --- |
| llama.cpp (local) | `http://127.0.0.1:8080/v1/chat/completions` |
| OpenAI | `https://api.openai.com/v1/chat/completions` |
| Groq | `https://api.groq.com/openai/v1/chat/completions` |
| DeepSeek | `https://api.deepseek.com/v1/chat/completions` |
| OpenRouter | `https://openrouter.ai/api/v1/chat/completions` |

## Repository layout

```
.
├── web/        Angular 19 app (primary interface)
│   └── src/app/core/   SRT parser, prompt, languages, providers, time tracker, HTTP service
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

- Support for additional subtitle formats (`.vtt`, `.ass`, `.sub`)
- Side-by-side preview and per-block editing in the web app
- Translation memory for character-voice consistency across a file
- General document/text translation beyond subtitles

## License

MIT — see [LICENSE](LICENSE).
