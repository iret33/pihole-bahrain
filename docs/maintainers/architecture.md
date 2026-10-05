# Sinko — architecture and contracts (3.0)

This file is the single source of truth for how the parts of Sinko talk to each other.
If code and this file disagree, fix one of them in the same change.

Sinko (Arabic: سينكو) is a parental-controls page and scheduler on top of Pi-hole v6.
It ships as free software for people with their own hardware, and as a ready-made, pre-flashed
Orange Pi Zero 3.

## Pieces

| Piece | Where | Runs as | Notes |
|---|---|---|---|
| Parent page | `web/` → `/var/www/html/index.html` + `/pb/*` | browser (phone) | static files served by Pi-hole's web server; talks only to the Pi-hole API on the same origin |
| `sinko` CLI + scheduler | `bin/sinko` → `/opt/sinko/bin/sinko`, symlink `/usr/local/bin/sinko` | root (systemd `sinko.service` runs `sinko run`) | Python 3 standard library only |
| Installer | `install.sh` | root | idempotent: install, update and migrate |
| Appliance tools | `tools/seal.sh`, `tools/firstboot.sh`, `systemd/sinko-firstboot.service` | root | prepare / personalise the ready-made image |
| Release build | `tools/build-release.sh`, `.github/workflows/release.yml` | CI | produces the three release assets |
| Counter service | `telemetry/` (Cloudflare Worker + D1) | Cloudflare | anonymous "boxes online" count, opt-in |
| Project site | `site/` → GitHub Pages | browser | landing page with live counters |

Pi-hole objects created by Sinko keep the **internal prefix `pb-`** (groups `pb-kids`, `pb-svc-<id>`, …; list
comments `pb:<id>`). The prefix is historical and is not user-facing; do not rename it (it would orphan existing
rules).

## Names, paths, environment

* Command `sinko`; repo `iret33/sinko` (`SINKO_REPO_SLUG`); units `sinko.service`, `sinko-lists.service`, `sinko-lists.timer`,
  `sinko-firstboot.service`; transient unit `sinko-update`.
* `/opt/sinko` (code; `/opt/sinko/src` is the last downloaded source), `/etc/sinko/config` (settings, shell-compatible
  `KEY=value`), `/var/lib/sinko/` (runtime data owned by root, see below), `/var/log/sinko-install.log`.
* Legacy (pihole-bahrain ≤ 2.2.x): `/opt/pihole-bahrain`, `/etc/pihole-bahrain`, units `pihole-bahrain*.service|timer`,
  command `pihole-bahrain`, config keys `PB_*`, page marker `<meta name="generator" content="pihole-bahrain">`.
  The installer migrates these (see *Migration*). `read_config` in the CLI also accepts legacy `PB_X` for any missing
  `SINKO_X`.
* Test/override hooks: `SINKO_ROOT` (installer fake root), `SINKO_APP_DIR`, `SINKO_CONFIG_FILE`, `SINKO_STATE_DIR`
  (default `/var/lib/sinko`), `SINKO_API_URL`, `SINKO_RELEASE_BASE`, `SINKO_RELEASE_API`, `SINKO_TELEMETRY_URL`.

### `/etc/sinko/config` keys

`SINKO_HOSTNAME`, `SINKO_LISTS_BASE`, `SINKO_REPO` (git URL), `SINKO_REPO_SLUG` (`owner/name`), `SINKO_REF`
(`latest` by default), `SINKO_IP`, `SINKO_TELEMETRY` (`1` / `0` / absent = not decided at install time),
`SINKO_TELEMETRY_URL` (empty = counter not configured), `SINKO_MDNS`, `SINKO_OS_UPDATES`.

### `/var/lib/sinko/` (root only, never reachable from the page)

| File | Purpose |
|---|---|
| `install-id` | random 128-bit hex used by the optional counter; created lazily, removed by `seal` |
| `handled.json` | `{"update": <nonce>, "check": <nonce>, "power": <nonce>}`: last request markers acted on |
| `update-result.json` | written by the update runner when it ends: `{"status":"ok|failed","from","to","error","at"}` |
| `cache/sinko-<version>.tar.gz` | the installed release and the newest older one (the rollback target); offline rollback |
| `lock` | the update runner's lock (the installer's own is `install.lock`: they must differ, the runner holds `lock` while it runs the installer) |
| `update-failure.json` | when and how the last update failed (retry rule: a transient failure may retry the same night after 30 minutes, others wait a week) |
| `counter-sent` | marks that the current `install-id` was sent to the counter (a never-sent id is just deleted when the counter is switched off) |
| `forget-pending` | the counter's id waiting for its "forget me" request to succeed (retried every ping interval; no pings meanwhile) |
| `removed` | set by `sinko remove` so a running scheduler does not repair what was removed on purpose; cleared by `setup` |
| `firstboot` | flag file: the ready-made image still has to personalise itself |

`/run/sinko/heartbeat.json` (memory, no card wear) is written by the scheduler after each good pass; the self-check reads it.

## Release assets

Tag `vX.Y.Z` with `VERSION` = `X.Y.Z` (CI checks). `tools/build-release.sh` builds, reproducibly:

* `sinko.tar.gz`: one top-level directory `sinko/` containing `bin/ lists/ web/ systemd/ tools/seal.sh tools/firstboot.sh install.sh
  uninstall.sh VERSION LICENSE NOTICE` (no tests, docs, site, telemetry or development tools).
* `sinko.tar.gz.sha256`: one line, `<hex>  sinko.tar.gz`.
* `install.sh`: byte-identical to the file in the tag (the one-liner).

Stable asset names, so these URLs work: `https://github.com/<slug>/releases/latest/download/<asset>` and
`https://github.com/<slug>/releases/download/vX.Y.Z/<asset>`. Every download of an asset is counted by GitHub
(`download_count`): that is the project's download count (README badge, site counter).

Latest-release metadata: `https://api.github.com/repos/<slug>/releases/latest` (`tag_name`, `html_url`,
`prerelease`, `draft`). Overridable with `SINKO_RELEASE_API`; asset base overridable with `SINKO_RELEASE_BASE`
(default `https://github.com/<slug>/releases`) so tests can serve fake releases from a local HTTP server.

`SINKO_REF`: `latest` (default) = newest release; `vX.Y.Z` = that release; anything else (a branch such as `master`)
= developer path: `git clone --depth 1 --branch <ref> $SINKO_REPO`.

## Installer contract (`install.sh`)

* `curl -fsSL https://github.com/<slug>/releases/latest/download/install.sh | sudo bash` (also still works from raw
  `master`).
* Fetches the release tarball + `.sha256`, verifies it (refuses on mismatch), extracts to `/opt/sinko/src`, then runs
  the `install.sh` shipped inside it (existing re-exec behaviour). `SINKO_SRC=<dir>` uses an already extracted tree and
  skips fetching (used by `sinko update`, tests and the image build). Always non-interactive when `SINKO_NONINTERACTIVE=1`.
* Idempotent. Exit status ≠ 0 on failure, progress to stdout, full log in `/var/log/sinko-install.log` (the generated
  parent password never reaches the log).
* `SINKO_TELEMETRY=1|0` records the install-time answer (interactive installs ask: default *no*; non-interactive
  installs without the variable record nothing, so the page asks later).
* Installs `avahi-daemon` so the box answers to `<hostname>.local` (`SINKO_MDNS=0` skips) and
  `unattended-upgrades` for Debian security updates (`SINKO_OS_UPDATES=0` skips; left alone when already installed).

### Migration from pihole-bahrain

Detected by `/opt/pihole-bahrain` or `/etc/pihole-bahrain` or the old units. Done by the new installer in this order:
read the old config (`PB_*` → `SINKO_*`, keep hostname/ip/lists/ref), stop + disable + delete the old units, install
the new files, `sinko setup` (re-registers every list under the new address, keeps groups, devices, rules, state),
install the new units, leave `/usr/local/bin/pihole-bahrain` as a symlink to `sinko` (with a one-line notice), and only at
the very end delete `/opt/pihole-bahrain` and `/etc/pihole-bahrain` (the running installer may live inside the old
`/opt/pihole-bahrain/src`). The old page (`generator` = `pihole-bahrain`) is replaced, never backed up as a user page.
Old boxes update themselves by running `pihole-bahrain update`, which fetches the new repo (GitHub redirects the old
name) and runs the new installer.

## Shared state (`pb-state` group description, JSON)

Read and written by the page **and** by the scheduler. Both normalise with `parse_state` (Python, `bin/sinko`) /
`parseState` (JS, `web/`), unknown top-level keys are dropped, so every key must be known to both.
`tests/fixtures/state-cases.json` is the executable spec: `tests/test_state_contract.py` and
`tests/state-contract.test.js` run the same file against both parsers.

```jsonc
{
  "v": 1,
  "timer": null, "schedule": {...}, "scheduleActive": false,      // unchanged since 2.x
  "update": {
    "auto": false,            // page: install updates by itself, 03:00–05:00 box time
    "request": null,          // page: marker of "Update now": the time of the request, ms on the box's clock
    "checkRequest": null,     // page: marker of "Check again" (the same kind of marker)
    "latest": null,           // scheduler: newer version available ("3.1.0") or null
    "notes": null,            // scheduler: https://github.com/<slug>/releases/tag/v3.1.0 (the page only links this)
    "checked": 0,             // scheduler: epoch seconds of the last successful check
    "status": "idle",         // scheduler: idle | running | ok | failed
    "from": null, "to": null, // versions of the current/last attempt
    "at": 0,                  // epoch seconds of the last status change
    "error": null             // short reason when failed (≤ 200 chars, no secrets)
  },
  "power": {"request": null, "action": null},   // page: "reboot" | "poweroff" with a request marker (a time, as above)
  "telemetry": {"on": null},                    // null = not asked, true/false = the parent's answer (authoritative)
  "community": null,                            // scheduler: {"online": n, "at": epoch} after a successful ping
  "setup": {"done": false}                      // page: first-run checklist dismissed
}
```

**Request protocol.** A request marker is the time of the request in milliseconds on the **box's** clock: the page takes
the box's time from the `Date` header of Pi-hole's answers, so a phone with a wrong clock is no problem. The scheduler acts
when it is non-null and different from the value stored in `/var/lib/sinko/handled.json`; it first stores the marker there,
then clears it in the state, then acts (so a reboot request cannot repeat after the box comes back). The page never decides
anything: it only asks. Nothing in the state is ever used as a URL, path or command; updates always come from the repo
configured in `/etc/sinko/config`.

**A request is for now.** While the box's clock is believed, a marker that is a time (10^11 or more) and lies more than 15
minutes before or after the box's clock is dropped like a handled one (cleared from the state, never acted on; the log says so). A
marker that is not a time stays opaque and is acted on once, as before. The case this exists for is a restored backup: the
restore puts the backup's `pb-state` back, with every request that was waiting when the backup was made, until the page writes
today's fields over it again (`keepBoxFields`), and a scheduler pass can land in between. A request this box never handled
(a backup from another box, or from before later requests) looks new to the "different from handled" rule; its age tells it
apart. `tests/test_page_scheduler.py` makes a pass land in exactly that window.

The scheduler writes the state only when something changed (a write makes Pi-hole reload), and re-reads right
before writing (existing pattern in `Controller.tick`) so it never overwrites what the page just wrote.

## Update flow

1. **Check** (scheduler, 2 minutes after start and then every 24 h, or on `checkRequest`): GET the release API
   with a 10 s timeout in a background thread (never block the tick). Ignore drafts and prereleases. Semver compare
   against `VERSION`. Offline or rate-limited = silently keep the old answer. Write `latest/notes/checked` if they
   changed or `checked` is older than a day.
2. **Run** (on `update.request`, or `auto` inside the 03:00–05:00 window when `latest` is set and this version has
   not failed in the last 7 days): minimum 5 minutes between runs. Set `status=running, from, to, at`, clear the
   request, then start `sinko update --yes --from-panel` as a detached transient unit (`systemd-run --unit sinko-update
   --collect`; fall back to a detached subprocess). The runner survives the scheduler restart the installer causes.
3. **`sinko update`**: download `sinko.tar.gz` + `.sha256` from the configured repo; verify the checksum; refuse any
   archive with a link, device, absolute or `..` member path, too many members or too much unpacked size, or without
   `sinko/VERSION`, `sinko/install.sh`, `sinko/bin/sinko`; refuse a version that is not newer unless `--force`; copy the
   tarball to `/var/lib/sinko/cache/` (the installed release plus the newest older one are kept); flush everything to disk;
   run the extracted `install.sh` with `SINKO_SRC` and `SINKO_NONINTERACTIVE=1`; then `sinko selfcheck --wait 90` in a **new
   process of the newly installed program**. If the installer or the self-check fails, **roll back** automatically by
   re-installing the previously cached version and checking that, and report `failed` with `rolledBack` = `true` only when
   the old version is back and passes its check (`false` when it is not, or there was no copy to go back to). A failure
   before the installer ran (download, checksum, space) is `rolledBack` = `true` only after a self-check of the untouched
   box passes. The stored `error` is short English for whoever helps, never a command. Finally write
   `update-result.json` (`status`, `from`, `to`, `error`, `at`, `rolledBack`, `transient`).
4. **Reconcile** (scheduler, every tick): if `status == running` and `update-result.json` exists (and is not older than the
   run), copy it into the state (`ok`/`failed`, `to`, `error`, `at`, `rolledBack`), delete the file, and clear `latest` when
   it is now installed. If `running` and the runner is gone (the update lock is free) for more than 3 minutes, a
   background self-check of the box decides: the new version in place and passing = `ok`; the old version still working =
   `failed` with `rolledBack` true; otherwise `failed` with `rolledBack` false ("did not finish").
5. `sinko rollback` re-installs the previous cached version by hand. `sinko update --ref vX.Y.Z` pins a release.

`sinko selfcheck [--wait N]` (no DNS probing, exit ≠ 0 on failure, one line per check, waits up to N seconds for
"starting" to become "ok"): API reachable and logged in with the CLI password, the `pb-*` groups exist, every catalog list is
registered, **the scheduler has run: the same process up for at least 20 s, at least 2 finished passes of this version, a
heartbeat under 60 s old, and not crash-looping** (a unit that is merely "active" for a second between crashes does not
pass), every file `index.html` loads exists and is not empty (and every file of the release's web folder when the source is
that version), and `pb/version.txt` equals `VERSION`.

## Power requests

`power.action` `reboot` → `systemctl reboot`; `poweroff` → `systemctl poweroff`, executed after the marker is stored
and the state cleared. The page warns that the children's internet stops while the box is off. **A request that arrives
while an update runs is dropped** (marker stored as handled, cleared, logged), never held and never fired later; the page
disables the two buttons while an update runs or is requested, and withdraws an unanswered request.

## Address watch

The scheduler compares the default-route IPv4 with `SINKO_IP` every 5 minutes; when it changed it runs the equivalent of
`sinko configure --ip <new> --hostname <name>` and rewrites `SINKO_IP`, so the local name keeps pointing at the box after
the router hands out a new address (and after a ready-made unit is switched on in a different network).

## Optional anonymous counter ("boxes online")

* Off until the parent says yes (`telemetry.on == true` in the state; a page card or the installer prompt asks;
  `sinko telemetry on|off|status|payload|reset-id` mirrors it). Endpoint from `SINKO_TELEMETRY_URL` (https, or http to
  loopback in tests); empty = not configured: nothing is ever sent, and `box.json` says `"counter": false`, so the page
  hides the card and the checklist question. Switching off sends the forget request below.
* Every 6 h ± 30 min (first one 5 minutes after start) in a background thread with a 10 s timeout:
  `POST {url}/v1/ping`, `Content-Type: application/json`, body ≤ 512 bytes:
  `{"id": "<32 lowercase hex>", "v": "3.0.0", "hw": "orangepi-zero3|raspberrypi|x86|other"}`.
  `id` is random, stored in `/var/lib/sinko/install-id`, derived from nothing on the device. Nothing else is sent: no
  domains, device names, addresses, children, rules, language or timezone.
  `hw` comes from `/proc/device-tree/model` ("Orange Pi Zero 3" → `orangepi-zero3`, "Raspberry Pi" → `raspberrypi`)
  or the CPU architecture (`x86_64` → `x86`), else `other`.
* Response: `{"online": <int>, "total": <int>}`; stored as `community` in the state when the numbers are sane.
* Worker (`telemetry/`) stores `id, first_seen, last_seen, version, hw, country` (country from Cloudflare's
  `CF-IPCountry`; the IP is never stored or logged by the Worker) in D1. Ignores a ping from an id seen < 10 minutes
  ago, rejects malformed bodies, purges ids unseen for 180 days (cron).
  * `GET /v1/stats` → `{"online","active7d","total","countries","versions":{},"hw":{},"downloads":<int|null>,"generatedAt"}`
    (online = seen within 12 h; downloads = sum of GitHub release asset downloads, cached 15 min; CORS `*`,
    `Cache-Control: public, max-age=60`).
  * `GET /badge/online.json`, `/badge/total.json`, `/badge/downloads.json`: shields.io endpoint JSON
    (`{"schemaVersion":1,"label":"…","message":"1.2k","color":"0F766E"}`).

## Parent page contract

* Same-origin Pi-hole API only (`/api/...`); logic stays dependency-free vanilla JS, English + Arabic (RTL), no
  build step. Every string exists in both languages, with the same `{placeholders}`; the copy test bans technical
  words outside the "how it works" sheet.
* New in 3.0: **My box** sheet (update, health, network addresses, change password, backup/restore, restart/shut
  down, anonymous counter, about), **first-run claim** screen, **setup checklist** card, update banner when a newer
  version exists.
* Pi-hole API calls used by the new features (from the FTL OpenAPI specs; copies in the maintainers' scratch notes,
  upstream: `pi-hole/FTL/src/api/docs/content/specs/*.yaml`):
  `GET /api/auth` (no `sid` + `session.valid` true ⇒ no password set ⇒ claim screen),
  `PATCH /api/config` with `{"config":{"webserver":{"api":{"password":"…"}}}}` (write-only property; the session is
  invalidated afterwards, so sign in again), `GET /api/info/system`, `/api/info/sensors`, `/api/info/version`,
  `/api/info/host`, `GET /api/teleporter` (zip), `POST /api/teleporter` (multipart `file` + `import` JSON), `GET
  /api/dns/blocking`.
* Backup = the whole Teleporter archive (it contains the password hash: the page says to keep it private).
  Restore imports **only** the gravity tables (`group, adlist, adlist_by_group, domainlist, domainlist_by_group,
  client, client_by_group`) and not `config`, so a restore never changes the address, upstreams or password.
* Update links: only `update.notes` values that `parseState` accepted are rendered as links.

## Ready-made image (Orange Pi Zero 3)

Golden-unit method: install Sinko on a fresh Armbian minimal (Debian Trixie) image, run `tools/seal.sh`, copy the SD
card to an `.img`, compress, flash many cards. Sealing removes everything that must be unique or private (SSH host keys,
machine-id, install-id, query history, parent password so the page offers the **claim screen**, shell history, logs),
locks the default root password / password SSH logins, removes Armbian's first-login wizard file, and arms
`sinko-firstboot.service`. First boot regenerates host keys, repairs the local name/address for the actual network
(`sinko configure`), refreshes `SINKO_IP`, then deletes the flag. `docs/product-image.md` is the seller's procedure.

## Things only real hardware can prove

Listed in `docs/hardware-test-checklist.md`; nothing in CI exercises: a real Pi-hole v6 (password config, teleporter,
sensors), systemd behaviour (`systemd-run`, reboot from the service), Armbian first boot, mDNS on phones, the
Orange Pi Zero 3's thermal sensor path, and a real GitHub release round trip.

## Amendments agreed after the first review (these override the text above where they differ)

**State**: `update.rolledBack` is `true` (the previous version was put back and passes the self-check), `false` (the update
failed and going back did not work, or there was nothing to go back to, or the runner died) or `null` (not known / no
failure). Only the page decides what a parent reads from it: "the previous version is back" is shown only when it is
`true`. `update.error` is technical text for whoever helps (English, at most 200 characters, **never a command to type**);
the page does not print it as the reason in the parent's language. Both parsers are strict (ASCII digits, whole-string
regular expressions, no NaN/Infinity tokens, `timer.snapshot.services` must be an object); `tests/fixtures/state-cases.json`
is the spec.

**`/pb/box.json`** (written by the box program as root, mode 644, atomically; read by the page without signing in):
`{"v":1,"version":"3.0.0","ip":"192.168.1.50","tz":"Asia/Bahrain","utcOffset":"+03:00","counter":true,"mdns":true,"at":1790000000}`.
`ip` is the default-route IPv4 (what the router's DNS setting needs; the page never puts a *name* there), `counter` is
"the counter address is configured", `mdns` is "avahi answers for this host", `at` is the box clock when it was written.
Written by `sinko box-info --write` (the installer calls it after the page swap; `sinko configure` and the address watch call
the same code) and refreshed by the scheduler every 5 minutes (only content changes or a 10-minute age cause a write: it is
also the **heartbeat**: the page treats a box.json whose `at` is more than 20 minutes behind the box's own clock as "the
scheduler is not running"). A missing file (an older box) disables every feature that needs it; nothing breaks.

**Counter: switching off forgets.** When the answer becomes "no" (page, `sinko telemetry off`, or `reset-id`), the box sends
`POST {url}/v1/forget` with `{"id":"<32 hex>"}` (same validation, same transport rules as a ping; answer `200 {"forgotten":true}`
whether or not the id was known), then deletes its `install-id`. If the request fails it keeps the id plus a
`forget-pending` marker in the state dir and retries every ping interval until it succeeds. The Worker deletes the row.
No shell is needed anywhere in this procedure (ready-made boxes have no login).

**Update source**: `SINKO_RELEASE_BASE` and `SINKO_RELEASE_API` must be https (http only to a loopback address under the test
hooks). The installer's and the updater's lock lives in the state dir, never in `/run/lock`. `sinko update --ref X` pins the box
(it is saved as `SINKO_REF`); `--ref latest` follows releases again; the command says so.

**Migration**: a legacy `PB_REF=master` is the 2.x default, not a choice: for the official project it becomes
`SINKO_REF=latest`. A kept lists address (the old `raw.githubusercontent.com/iret33/pihole-bahrain/...`) is **kept**, so
no list is deleted or re-registered during migration (zero unfiltered window; GitHub's rename redirect serves it); a lists
address inside the old app folder is rewritten to `/opt/sinko/lists`. The old scheduler is stopped (not disabled) until the
new one runs. A pre-existing `^.*$` deny rule that is not Sinko's (by comment) keeps its comment and enabled flag, gains
Sinko's groups, and is never deleted by `remove`.
