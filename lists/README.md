# Parental-control block lists

16 category block lists, one per service, in [Pi-hole ABP format](https://docs.pi-hole.net/database/gravity/other-formats/)
(one `||domain^` entry per line).

| File | Service |
|---|---|
| `chatgpt.txt` | ChatGPT |
| `discord.txt` | Discord |
| `facebook.txt` | Facebook |
| `instagram.txt` | Instagram |
| `microsoft-teams.txt` | Microsoft Teams |
| `netflix.txt` | Netflix |
| `playstation.txt` | PlayStation |
| `roblox.txt` | Roblox |
| `snapchat.txt` | Snapchat |
| `steam.txt` | Steam |
| `telegram.txt` | Telegram |
| `tiktok.txt` | TikTok |
| `whatsapp.txt` | WhatsApp |
| `x-twitter.txt` | X / Twitter |
| `xbox.txt` | Xbox |
| `youtube.txt` | YouTube |

## How updates flow to installs

These lists are registered on each Pi-hole as **URL-based adlists** pointing at
this repo's raw URLs (`https://raw.githubusercontent.com/iret33/pihole-bahrain/master/lists/<name>.txt`).

Pi-hole re-fetches URL adlists on every gravity run (weekly by default). So to
push an update to every install:

1. Edit the relevant `*.txt` here (commit + push to `master`).
2. Each install picks it up on its next gravity run — or force it immediately on
   a given Pi-hole with `pihole -g`.

> **Note:** the repo must be **public** for the raw URLs to resolve. A private
> repo returns 404 and the lists will not download.

## Fresh install

`install.sh` installs Pi-hole (if needed), drops in the parental page, and then
runs `lists/register.sh`, which:

1. creates a **Kids** group (the service lists are scoped to it),
2. registers all 16 lists as URL-based block lists in that group,
3. triggers a gravity run.

`register.sh` is idempotent — re-run it anytime to (re)register the lists.

## Format

- One entry per line: `||example.com^` blocks `example.com` and all subdomains.
- Blank lines are ignored. Do not add comments or whitespace on the same line as
  a domain.
