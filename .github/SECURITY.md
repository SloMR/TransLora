# Security

## Reporting

Report privately through
[GitHub Security Advisories](https://github.com/SloMR/TransLora/security/advisories/new),
not a public issue.

Expect an acknowledgement within a week. Fixes ship as a normal
`web/x.y.z` or `cli/x.y.z` tag; only the newest tag of each component is patched.

## What TransLora does with your key and your subtitles

By design, not bugs:

- **There is no TransLora server.** The web app is static files; every request
  goes from your browser straight to the API endpoint you configured. The CLI
  behaves the same way from your machine.
- **The API key is never persisted.** The web app keeps it in memory for the
  session only — `localStorage` holds provider/model/language settings and
  nothing else. The CLI reads it from `--api-key` or `$TRANSLORA_API_KEY`.
- **Subtitle text is sent to the provider you pick.** That is the whole job. If
  the content is sensitive, point the app at a local endpoint.
- **A custom endpoint is trusted with the key.** Choosing "Custom / Local" sends
  your `Authorization` header wherever you typed. Check the URL.

In scope and worth reporting: a key or subtitle body leaking anywhere other
than the configured endpoint (a log, a URL, a third party), the shipped nginx
config or Docker images weakening the browser's isolation of the app, XSS via
subtitle content or a provider response, and anything in
`scripts/create-releases.sh` that a crafted tag or commit message can turn into
command execution.
