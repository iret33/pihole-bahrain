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

## How it works

The repo is **private**, so installs use **file-based** lists (not public raw
URLs — nothing to leak, and no tokens stored anywhere).

`register.sh` copies these `*.txt` files into `/etc/pihole/lists/` and registers
each as a `file://` blocklist in the **Kids** group, then runs gravity.

## Updating the lists

1. Edit the relevant `*.txt` here and commit + push to `master`.
2. On each Pi-hole, pull and re-register:

   ```bash
   git pull
   sudo bash lists/register.sh lists
   ```

`register.sh` is idempotent — re-running it refreshes the files, skips already-
registered lists, and rebuilds gravity.

## Fresh install

`install.sh` installs Pi-hole (if needed), drops in the parental page, and then
runs `lists/register.sh`, which:

1. creates a **Kids** group (the service lists are scoped to it),
2. copies the 16 lists into `/etc/pihole/lists/` and registers them as `file://`
   block lists in that group,
3. triggers a gravity run.

## Format

- One entry per line: `||example.com^` blocks `example.com` and all subdomains.
- Blank lines are ignored. Do not add comments or whitespace on the same line as
  a domain.
