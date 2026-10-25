# Security Policy

## Scope

This project reads **your own** Discord client data on **your own** machine: it unwraps
the DPAPI-protected key from `Local State`, decrypts the token stored in the LevelDB
folder, and keeps that token in `config.json` next to the program.

That design has consequences worth stating plainly.

## What the tool does with your data

| Data | Where it goes |
|---|---|
| Discord token | `config.json` (plain text, gitignored), then `Authorization` headers to `discord.com` only |
| Discord path, nickname, user id | `config.json` (gitignored) |
| VPN node list | `vpns.json` and `.vpn/` (gitignored) |

Nothing is uploaded anywhere except the requests the tool has to make to Discord, and the
download of the public node lists from GitHub.

## Protecting yourself

- **Never commit `config.json`.** `.gitignore` covers it, but `.gitignore` does not protect
  a ZIP you share by hand — delete the file first.
- **Treat the token like a password.** Anyone who has it has your account until you change
  your Discord password (which invalidates all tokens).
- **Do not paste your token into chat, issues or pull requests.** If it leaked, change the
  Discord password immediately.
- Run the tool from a folder you control; do not put it in a synced cloud directory.

## Reporting a vulnerability

If you find a flaw in this code — an unsafe file write, a crash that leaks the token into
logs, a parser bug reachable from a remote node list — please report it privately:

1. Open a [private security advisory](https://github.com/Apatiym/discord-username-checker/security/advisories/new), or
2. Contact the maintainer through the profile at <https://github.com/Apatiym>.

Please include:

- a description of the issue and its impact,
- the steps to reproduce (a minimal input is ideal),
- the version or commit you tested.

You can expect an initial response within a few days. Please give a reasonable window for a
fix before publishing details.

## Out of scope

- Discord rate limits, captchas, or ToS enforcement — these are not vulnerabilities here.
- Anything that requires an already-compromised machine or an already-stolen token.
- The fact that `config.json` stores the token in plain text: it is documented, the file is
  gitignored, and the tool needs to read it back on every run.
