# Contributing

Thanks for taking the time to contribute. This is a small, dependency-free project, and
the goal is to keep it that way.

## Before you start

- Open an issue for anything larger than a typo or a one-line fix. It is easier to agree
  on the approach before the code exists.
- Be decent to each other — see [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
- For security issues, **do not open a public issue** — read [SECURITY.md](SECURITY.md).

## Development setup

```bat
git clone https://github.com/Apatiym/discord-username-checker.git
cd discord-username-checker
python -m unittest discover -s tests -v
```

There is nothing to install: the project uses the standard library only. `sing-box` is
optional and only needed if you want to exercise the VPN pool.

## Ground rules

| Rule | Why |
|---|---|
| Standard library only | The "no dependencies" promise is part of the design |
| Windows-first, importable everywhere | Token extraction needs DPAPI; the other modules must still import on Linux for CI |
| No hardcoded personal data | Paths, tokens and nicknames are detected or asked for at runtime |
| Comments explain *why* | The code already says what it does |
| Keep the four-module split | `ds_username.py` / `net.py` / `creds.py` / `ui.py` |

## Code style

- Follow the surrounding code: four spaces, ~88 columns, `snake_case`, short helpers.
- No emoji anywhere — the console UI is ASCII plus ANSI colour.
- Any user-visible string is in English.
- Prefer a small function with a clear name over a clever one-liner.

The full style config lives in `pyproject.toml` (ruff, `E`/`F`/`W`, line length 100).

## Tests

Tests live in `tests/` and must not touch the network or a real Discord account. If you
add behaviour, add a test:

```bat
python -m unittest discover -s tests -v
```

If a test needs `config.json`, point `ds_username.CONFIG_FILE` at a temporary file — never
at the real one.

## Commits

Conventional commits, imperative mood, short subject:

```text
feat: retry a failed node before dropping it from the pool
fix: keep the lock margin when the clock jumps backwards
docs: explain why the account channel ignores the VPN pool
```

## Pull requests

1. Fork, branch from `main` (`feat/…`, `fix/…`, `docs/…`).
2. Keep the diff focused — one concern per PR.
3. Make sure `python -m unittest discover -s tests -v` passes.
4. Fill in the PR template: what changed, why, and how you verified it.
