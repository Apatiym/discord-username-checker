# Discord Short Username Checker

A Windows console tool that hunts short Discord usernames. It prints `taken`
in red and `free` in green, and can set the nick without leaving the terminal.

```
  taken      q7x     red     already taken
  free       k2v     green   available
  invalid    a b     gray    not a valid name
```

Block-character logo, croissant-colored per-character gradient (24-bit ANSI with
a 256-color fallback and a plain ASCII logo when unicode is unavailable),
arrow-key menu, no emoji, no third-party packages.

## Requirements

- Windows 10 or newer - token extraction uses DPAPI
- Python 3.8+ (tested on 3.12)
- optional: `sing-box` for the VPN pool - it ships with the Happ client

## Run

```
start.bat
```

or

```
python ds_username.py
```

`config.json` is created on the first run and is gitignored: it holds the token.

## Menu

Arrow keys or `W`/`S` move, `Enter` picks, `Q` / `Esc` / `0` quits. Number keys
jump straight to an item.

| Item | What it does |
|---|---|
| find short names | generates random names and scans them |
| check one name | one name, one answer |
| check a list | reads a file (default `names.txt`), one name per line |
| settings | path, token, workers, mode, IP channel, VPN |
| quit | |

Duplicates, `@`, spaces and overlong lines are dropped from list files.

## Two channels, two limits

| Channel | Endpoint | Budget | Lock |
|---|---|---|---|
| account | `users/@me/pomelo-attempt` | ~150 checks | 5-6 min |
| IP | `unique-username/username-attempt-unauthed` | ~19 checks | 25-35 min |

The program takes the account channel first, falls back to IP, and stops when
both are burned, printing what is left on each.

A request made during a lock **extends** it - measured: 5 minutes turned into
32. That is why a lock is remembered with a 3-minute margin and nothing is sent
until it expires.

## Speed

The program is not the bottleneck: connections are kept alive, which brings a
request down to ~70-140 ms instead of ~400 ms with `urllib`.

| Mode | Delay | Behaviour |
|---|---|---|
| safe (default) | 1.0 s, AIMD down to 0.4 s | rides the edge of the limit |
| fast | 0 | burst, hits a lock after ~150 checks |
| custom | any | your number |

Speed is regulated like TCP: each success shaves 0.012 s off the delay, a soft
`429` multiplies it by 2.2. Safe mode never drops below 0.4 s, otherwise it
turns into a burst again. The delay reached at the end of a run is saved and
the next run starts from there.

## VPN pool

`settings -> vpn` can put every request behind a different IP, using the public
lists from [igareck/vpn-configs-for-russia](https://github.com/igareck/vpn-configs-for-russia).

Python cannot speak these protocols, so `sing-box` does the tunneling:

```
our code -> HTTP proxy 127.0.0.1:PORT -> sing-box outbound -> node -> Discord
```

The program downloads the lists, parses VLESS (including Reality), Trojan and
Hysteria2, and writes one sing-box config where inbound port *i* is routed to
outbound *i*. Every worker then picks its own port, so parallel workers really
do use different IPs. Nodes that stop answering are dropped from the rotation;
alive ones are remembered in `vpns.json` (gitignored) to make the next start
faster. A cold start takes ~8-11 s, a warm one ~8 s.

Be realistic about what this buys:

- the **IP channel** is limited per IP, so N nodes multiply its budget;
- the **account channel** is limited per token - no amount of IPs resets it.

Free public nodes are unstable: at the time of writing 58 nodes parsed and
14-16 passed traffic, giving 13-14 distinct egress IPs.

`settings -> vpn` has three actions: toggle, pool size (1-40), refresh list.

## Token

`settings -> token -> find`, and the program also tries on start.

The client folder is found automatically - stable, PTB, Canary, Dev, whichever
actually has `Local Storage\leveldb`. Nothing is hardcoded, so the program works
on any machine. If nothing is found it asks once on start; `settings -> path`
has three options: auto-detect, enter manually, keep.

That field accepts whatever you paste: the data folder, the install folder
(`AppData\Local\Discord`), `Local State`, the `leveldb` folder itself, a quoted
path, `%APPDATA%\discord`. All of them resolve to the same folder.

Then:

1. `%APPDATA%\discord\Local State` -> `os_crypt.encrypted_key`
2. `CryptUnprotectData` (DPAPI, ctypes) -> AES-256-GCM key
3. `%APPDATA%\discord\Local Storage\leveldb\*.ldb|*.log` -> `dQw4w9WgXcQ:v10...`
4. AES-256-GCM decrypt, tag verified
5. `GET /users/@me` confirms the token and shows the nick

Every client is checked: `discord`, `discordptb`, `discordcanary`,
`discorddevelopment`. `...\AppData\Local\Discord` is only an install folder -
the data lives next door in `...\AppData\Roaming\discord`.

`config.json` stores the token in plain text. It is gitignored for that reason.

## Privacy

- Nothing personal is in the repository: the Discord folder is detected, not
  hardcoded, and `config.json` (path, token, nick) is gitignored.
- `config.json` is plain text next to the program and is the only file holding
  your account. To wipe it: `settings -> token -> forget`, or delete the file.
- If you zip or share the folder, delete `config.json` first - `.gitignore`
  protects GitHub, not a zip.
- `vpns.json` and `.vpn/` are local caches as well, both gitignored.

## Config

| Key | Meaning |
|---|---|
| `mode` | `safe`, `fast` or `custom` |
| `delay` | seconds between checks |
| `workers` | parallel workers |
| `use_ip` | allow the IP channel when a token exists |
| `vpn` | `{"enabled": bool, "size": int}` |
| `discord_path` | client data folder, detected on the first run |
| `token`, `me` | account |
| `locks` | channel -> timestamp |

## Files

| File | What it does |
|---|---|
| `ds_username.py` | menu, scanning, nick change |
| `ui.py` | logo, gradient, animations |
| `net.py` | keep-alive client, proxy detection, VPN pool |
| `creds.py` | DPAPI + AES-256-GCM token extraction |
| `start.bat` | launcher |
| `names.txt` | sample list |

## Reality of short names

Measured on one account:

| Length | Free out of random |
|---|---|
| 3 | 0 of 25 |
| 4 | 0 of 12 |
| 5 | 9 of 10 |
| 6 | 10 of 10 |

3-4 character names were claimed years ago; brute force there just spins.
5 characters is the realistic target. Better still: check the exact name you
want with "check one name".

## Notes

- A captcha on the nick change cannot be bypassed - do it in the client.
- Use this on your own account. Discord rate limits and the Terms of Service
  still apply; the tool tries to stay under the limits, not to defeat them.
- `NO_ANIM=1` disables animations, `NO_TRUECOLOR=1` forces 256 colors.
