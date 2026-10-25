<div align="center">

# Discord Short Username Checker

**A terminal workbench for hunting short Discord handles.**
Colour-coded results, rate-limit pacing, optional multi-IP pool — no third-party packages.

[![CI](https://github.com/Apatiym/discord-username-checker/actions/workflows/ci.yml/badge.svg)](https://github.com/Apatiym/discord-username-checker/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2B-0078D6?logo=windows&logoColor=white)](#requirements)
[![License](https://img.shields.io/badge/license-MIT-2ea44f)](LICENSE)
[![Dependencies](https://img.shields.io/badge/dependencies-none-success)](#requirements)

**English** · [Русский](README.ru.md)

</div>

---

## Overview

This is a single-purpose console application: it asks Discord whether a username is
still available, and it does so as fast as the rate limits allow without tripping
them. Results are printed as `taken` in red and `free` in green, and the shortest
free name can be written to your profile without leaving the terminal.

It was built around three constraints that most scripts ignore:

1. **Discord rate-limits usernames hard.** Two different channels exist with two
   different budgets, and a request sent during a lock *extends* the lock.
2. **Python is slow at TLS.** A fresh handshake per request costs ~400 ms. Keep-alive
   brings that to ~70–140 ms, which is faster than the API can answer.
3. **Your token is your account.** Nothing is hardcoded, nothing is uploaded, and the
   only file that ever holds the token is gitignored.

## Features

| | |
|---|---|
| **Two-state results** | `taken` in red, `free` in green, `invalid` in grey — no ambiguity |
| **Keyboard-first menu** | arrows or `W`/`S` to move, `Enter` to pick, number keys to jump |
| **Real per-character gradient** | 24-bit ANSI with a 256-colour fallback and a plain-ASCII mode |
| **Flicker-free animations** | block logo, sweep, reveal, single-line live progress |
| **AIMD pacing** | additive decrease on success, multiplicative backoff on `429` — like TCP |
| **Channel awareness** | tracks the account channel and the IP channel separately, with their own locks |
| **Auto-detected client** | finds the Discord data folder (stable / PTB / Canary / Dev), or you point at it |
| **DPAPI + AES-256-GCM** | token is decrypted locally from the client's LevelDB, never typed |
| **Optional VPN pool** | one egress IP per worker through public VLESS / Trojan / Hysteria2 nodes |
| **Zero dependencies** | standard library only; `sing-box` is optional and only for the pool |

## Preview

<img src="docs/demo.svg" alt="The menu with a gradient block logo, a red 'taken' line, a green 'free' line and a channel lock timer" width="560">

*Rendered from the program's own output: gradient logo, colour-coded results, lock timer.*

## Requirements

| | |
|---|---|
| OS | Windows 10 or newer — token extraction uses DPAPI |
| Python | 3.10+ (tested on 3.12), standard library only |
| Optional | [`sing-box`](https://sing-box.sagernet.org/) for the VPN pool — it ships with the Happ client |

Linux and macOS are not supported for the token step; the modules still import and the
unit tests run there (that is what CI checks).

## Installation

```bat
git clone https://github.com/Apatiym/discord-username-checker.git
cd discord-username-checker
start.bat
```

Or without cloning — download the ZIP, unpack, run `start.bat`. Or directly:

```bat
python ds_username.py
```

`start.bat` finds a Python 3 interpreter (`LOCALAPPDATA`, then `py -3`, then `python`),
sets the code page to UTF-8, and pauses on error.

On the first run `config.json` is created from built-in defaults. It is gitignored
because it ends up holding your token.

## Usage

### Menu

| Item | Action |
|---|---|
| `find short names` | generates random names of a chosen length and scans them in parallel |
| `check one name` | one name, one answer |
| `check a list` | reads a file (default `names.txt`), one name per line |
| `settings` | path, token, workers, mode, IP channel, VPN |
| `quit` | |

When a free name is found it is offered for the profile; `Enter` applies it, anything
else keeps the current nick. List files are sanitised: blank lines, duplicates, `@`,
spaces and overlong entries are dropped.

### Settings

| Group | What you can change |
|---|---|
| `path` | auto-detect, enter manually, or keep the current Discord folder |
| `token` | find automatically, paste manually, or forget |
| `workers` | 1–16 parallel workers |
| `mode` | `safe` (AIMD, default), `fast` (no delay), `custom` |
| `ip channel` | allow the small per-IP channel when a token exists |
| `vpn` | toggle the pool, set its size (1–40), refresh the node list |

## How it works

### Two channels, two limits

| Channel | Endpoint | Budget | Lock after exhaustion |
|---|---|---|---|
| account | `users/@me/pomelo-attempt` | ~150 checks | 5–6 min |
| IP | `unique-username/username-attempt-unauthed` | ~19 checks | 25–35 min |

The program prefers the account channel and falls back to the IP channel. When both
are burned it stops and prints the remaining time on each.

A request sent **during** a lock extends it — measured: 5 minutes became 32. That is
why a lock is stored with a 3-minute safety margin and nothing is sent until it expires.

### Speed

The program is not the bottleneck: connections are kept alive, so a request costs
~70–140 ms instead of ~400 ms with `urllib`.

| Mode | Delay | Behaviour |
|---|---|---|
| `safe` *(default)* | 1.0 s, AIMD down to 0.4 s | rides the edge of the limit |
| `fast` | 0 | burst; hits a lock after ~150 checks |
| `custom` | your number | — |

Pacing is regulated like TCP congestion control: every success shaves 0.012 s off the
delay, a soft `429` multiplies it by 2.2. Safe mode never drops below 0.4 s — below
that it becomes a burst again. The delay reached at the end of a run is saved, so the
next run starts from there.

### Token

`settings → token → find`; the program also tries on start.

1. `%APPDATA%\discord\Local State` → `os_crypt.encrypted_key`
2. `CryptUnprotectData` (DPAPI, via `ctypes`) → AES-256-GCM key
3. `%APPDATA%\discord\Local Storage\leveldb\*.ldb|*.log` → `dQw4w9WgXcQ:v10…`
4. AES-256-GCM decrypt with tag verification
5. `GET /users/@me` confirms the token and prints the nick

Every client is checked: `discord`, `discordptb`, `discordcanary`, `discorddevelopment`.
`…\AppData\Local\Discord` is only the *install* folder — the data lives next door in
`…\AppData\Roaming\discord`.

The folder is found automatically; nothing is hardcoded, so the program works on any
machine. If nothing is found it asks once, and `settings → path` accepts the data
folder, the install folder, `Local State`, the `leveldb` folder itself, a quoted path,
or `%APPDATA%\discord` — all resolve to the same place.

### VPN pool

`settings → vpn` can put every request behind a different IP, using the public lists
from [igareck/vpn-configs-for-russia](https://github.com/igareck/vpn-configs-for-russia).

Python cannot speak these protocols, so `sing-box` does the tunnelling:

```text
our code → HTTP proxy 127.0.0.1:PORT → sing-box outbound → node → Discord
```

The program downloads the lists, parses VLESS (including Reality), Trojan and
Hysteria2, and writes one sing-box config where inbound port *i* routes to outbound
*i*. Each worker picks its own port, so parallel workers really do use different IPs.
Nodes that stop answering are dropped from the rotation; healthy ones are remembered in
`vpns.json` (gitignored) to speed up the next start. Cold start ≈ 8–11 s, warm ≈ 8 s.

Be realistic about what this buys:

- the **IP channel** is limited per IP, so *N* nodes multiply its budget;
- the **account channel** is limited per token — no number of IPs resets it.

Free public nodes are unstable: at the time of writing, 58 nodes parsed, 14–16 carried
traffic, giving 13–14 distinct egress IPs.

## Configuration

`config.json`, created on first run and gitignored:

| Key | Meaning |
|---|---|
| `mode` | `safe`, `fast` or `custom` |
| `delay` | seconds between checks |
| `workers` | parallel workers |
| `use_ip` | allow the IP channel when a token exists |
| `vpn` | `{"enabled": bool, "size": int}` |
| `discord_path` | client data folder, detected on first run |
| `token`, `me` | account credentials and cached profile |
| `locks` | channel → timestamp of lock expiry |

## Architecture

```text
ds_username.py   menu, scanning, nick change, settings
├── net.py       keep-alive HTTP client, proxy detection, sing-box pool
├── creds.py     DPAPI + AES-256-GCM token extraction
└── ui.py        logo, per-character gradient, animations
```

`ds_username.py` owns the flow; the other three are libraries with no side effects at
import time. That split keeps every file under ~1500 lines and makes the network and
crypto layers testable in isolation.

| File | Responsibility |
|---|---|
| `ds_username.py` | CLI, menu, `Pacer`, `Gate`, channel selection, scanning, profile update |
| `net.py` | `Session`, `Client` (pool rotation), `Pool`, URI parsers, sing-box config builder |
| `creds.py` | AES-GCM decrypt, DPAPI unwrap, LevelDB scan |
| `ui.py` | palette, gradient, `Loader`, `ScanLine`, `reveal`, `sweep` |
| `tests/` | offline unit tests (no network, no Discord account) |
| `docs/demo.svg` | the preview image above, drawn from real program output |

## Development

```bat
python -m unittest discover -s tests -v
python -m py_compile ds_username.py net.py ui.py creds.py
```

The tests never touch the network and never read your real `config.json` — they patch
the config path to a temporary file.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `token not found` | You are not signed in to the Discord client, or it is a portable build. Sign in once, or paste the token in `settings → token`. |
| `path not found` | The client was never started on this machine. Point `settings → path` at `%APPDATA%\discord`. |
| `SSL: WRONG_VERSION_NUMBER` | Direct access to `discord.com` is blocked. The client auto-detects a local proxy; start your VPN/proxy client first. |
| `account lock 5m` | Normal. Wait it out — sending during a lock extends it. |
| `nothing free` | The channel is exhausted; the footer shows the remaining time. |
| Colours look wrong | `NO_TRUECOLOR=1` forces the 256-colour palette. |
| Animations are slow | `NO_ANIM=1` disables them. |

## Reality check

Measured on one account with random names:

| Length | Free out of random |
|---|---|
| 3 | 0 of 25 |
| 4 | 0 of 12 |
| 5 | 9 of 10 |
| 6 | 10 of 10 |

Three- and four-character names were claimed years ago; brute-forcing them mostly spins.
Five characters is the realistic target, and the best strategy is to check the exact
name you want rather than random ones.

## Limitations

- A captcha on the nick change cannot be bypassed — do it in the client.
- `config.json` stores the token in plain text. It is gitignored; if you share a ZIP,
  delete it first.
- The VPN pool only helps the IP channel. The account channel is per token.
- Public nodes are free and therefore unreliable; a pool can shrink between runs.

## Disclaimer

Use this on **your own** account. Discord's rate limits and Terms of Service still
apply: the tool tries to stay *under* the limits, not to defeat them. Automated
username squatting may violate the ToS — you are responsible for how you use this.

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
For security reports, read [SECURITY.md](SECURITY.md) first.

## License

[MIT](LICENSE) © Apatiym
