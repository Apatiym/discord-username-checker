# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] — 2026-10-25

First public release.

### Added

- Interactive menu: arrows or `W`/`S` to move, `Enter` to pick, number keys to jump.
- Username scanning with `taken` in red and `free` in green, `invalid` in grey.
- Random name generation, single-name check, and list-file check (`names.txt`).
- Automatic profile update with the shortest free name found.
- Separate tracking of the account channel (`pomelo-attempt`) and the IP channel
  (`username-attempt-unauthed`), each with its own lock and safety margin.
- AIMD pacing (`Pacer`): −0.012 s per success, ×2.2 on a soft `429`, floor of 0.4 s in
  safe mode; the delay is persisted between runs.
- Keep-alive HTTP client with automatic local proxy detection.
- Token extraction from the Discord client: DPAPI unwrap, AES-256-GCM decrypt, LevelDB
  scan, verified with `GET /users/@me`.
- Automatic Discord folder detection across stable, PTB, Canary and Dev clients, with a
  manual path fallback that accepts the data folder, install folder, `Local State`, the
  `leveldb` folder, quoted paths and `%APPDATA%\discord`.
- Optional VPN pool over public VLESS / Trojan / Hysteria2 lists, driven by `sing-box`
  with one inbound port per worker; unhealthy nodes are dropped, healthy ones cached.
- Per-character 24-bit gradient with a 256-colour fallback and a plain-ASCII logo.
- Flicker-free animations: block logo, sweep, reveal, single-line live progress.
- `NO_ANIM` and `NO_TRUECOLOR` environment switches.

### Security

- `config.json`, `vpns.json`, `.vpn/` and `*.tmp` are gitignored; no personal paths or
  credentials are committed.
- The Discord folder is detected at runtime instead of being hardcoded.

[Unreleased]: https://github.com/Apatiym/discord-username-checker/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Apatiym/discord-username-checker/releases/tag/v1.0.0
