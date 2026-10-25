## What changed

<!-- One paragraph. Link the issue if there is one: "Closes #12". -->

## Why

<!-- The behaviour before, and what was wrong with it. -->

## How it was verified

- [ ] `python -m unittest discover -s tests -v` passes
- [ ] `python -m py_compile ds_username.py net.py ui.py creds.py` passes
- [ ] `ruff check .` passes
- [ ] Tried it in a real `cmd` window (if the change touches the UI)

## Checklist

- [ ] Standard library only — no new dependencies
- [ ] No personal data added (paths, tokens, nicknames)
- [ ] User-visible strings are in English, no emoji
- [ ] `README.md` and `README.ru.md` updated if behaviour changed
- [ ] `CHANGELOG.md` updated under `[Unreleased]`

## Notes for the reviewer

<!-- Anything non-obvious: trade-offs, things you deliberately left out. -->
