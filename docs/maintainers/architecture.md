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
| `gravity.lock` | the flock that every `pihole -g` which Sinko starts takes first (the program's own runs, and `sinko-lists.service` through `flock(1)`), so two runs never rebuild Pi-hole's one temporary database together; a run waits for the one before it for up to 30 minutes. Pi-hole's own weekly run and the page's restore cannot take it |
| `repair.json` | when the last `sinko repair` was started: the scheduler starts one at most once an hour, and its restart forgets everything else |
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

* `curl --proto '=https' --proto-redir '=https' -fsSL https://github.com/<slug>/releases/latest/download/install.sh | sudo bash`
  (also still works from raw `master`). This one hardened form is the only one written anywhere (README, docs, the website):
  `--proto-redir` keeps a redirect from leaving https.
* Fetches the release tarball + `.sha256`, verifies it (refuses on mismatch), extracts to `/opt/sinko/src`, then runs
  the `install.sh` shipped inside it (existing re-exec behaviour). `SINKO_SRC=<dir>` uses an already extracted tree and
  skips fetching (used by `sinko update`, tests and the image build). Always non-interactive when `SINKO_NONINTERACTIVE=1`.
* Idempotent. Exit status ≠ 0 on failure, progress to stdout, full log in `/var/log/sinko-install.log` (the generated
  parent password never reaches the log).
* `SINKO_TELEMETRY=1|0` records the install-time answer (interactive installs ask: default *no*, **and only when the program
  says a counter address is configured** (`sinko box-info` → `"counter": true`); without one nothing is asked or saved;
  non-interactive installs without the variable record nothing, so the page asks later).
* Free space: more than **1 GB** for a first installation, more than **200 MB** for an update, a rollback or a move from
  pihole-bahrain (the scheduler's unit or what pihole-bahrain left says which); the refusal exits with status 75 and changes
  nothing. `bin/sinko` asks for the same 200 MB (`MIN_FREE_BYTES`) before it starts the installer.
* The parent password reaches Pi-hole by its API (`PATCH /api/config`, from standard input, never in a program's arguments)
  only while Pi-hole **has no password** yet: FTL refuses config changes from a command-line session (403), so there is no
  `cli_pw` sign-in. Otherwise `pihole setpassword`, whose argument list holds the password for a moment. An update never
  chooses a password. `docs/install.md` says both.
* Apt and dpkg answers that are read are asked for in the C locale (`LC_ALL=C`), and "installed" means
  `dpkg-query` says `install ok installed`.
* Installs `avahi-daemon` so the box answers to `<hostname>.local` (`SINKO_MDNS=0` skips), `unattended-upgrades` for Debian
  security updates (`SINKO_OS_UPDATES=0` skips; left alone when already installed, switched on or off) and, when the box has
  no time service at all, `systemd-timesyncd` (no battery-backed clock; another service that is installed but off is left to
  the owner, with a warning).
* `index.html` is installed with `@VERSION@` replaced by the release's version in the addresses of the page's scripts and
  style sheet (`/pb/app.js?v=3.0.0`) and in `<meta name="sinko-version">`: Pi-hole's web server lets a browser keep a static
  file for an hour, so every release changes every address. The web server ignores the query string.

### Migration from pihole-bahrain

Detected by `/opt/pihole-bahrain` or `/etc/pihole-bahrain` or the old units. Done by the new installer in this order:
read the old config (`PB_*` → `SINKO_*`, keep hostname/ip/lists/ref), stop + disable + delete the old units, install
the new files, `sinko setup` (re-registers every list under the new address, keeps groups, devices, rules, state),
install the new units, leave `/usr/local/bin/pihole-bahrain` as a symlink to `sinko` (with a one-line notice), and only at
the very end delete `/opt/pihole-bahrain` and `/etc/pihole-bahrain` (the running installer may live inside the old
`/opt/pihole-bahrain/src`). The old page (`generator` = `pihole-bahrain`) is replaced, never backed up as a user page.
Old boxes update themselves by running `pihole-bahrain update`, which clones `master` into `/opt/pihole-bahrain/src` (GitHub
redirects the old name) and runs the `install.sh` it finds there. When that checkout is inside the old program folder and the
box follows the releases (`SINKO_REF` latest or a version), the installer does not install the checkout: it fetches and
verifies the release first and lets the release's own installer do the work, so the box ends with a checksummed version, an
`/opt/sinko/src` and a rollback copy. If the release cannot be had (none published yet, no connection) it installs the checkout
as before and says so. A git checkout anywhere else, and a developer's `SINKO_REF=master`, are installed as they are.

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

1. **Check** (scheduler, 2 minutes after every start of the scheduler (the start after a power cut and the restart the
   installer causes included; nothing is persisted) and then every 24 h, or on `checkRequest`): GET the release API
   with a 10 s timeout in a background thread (never block the tick). Ignore drafts and prereleases. Semver compare
   against `VERSION`. Offline or rate-limited = silently keep the old answer, and ask again after `CHECK_RETRY` (30 minutes)
   plus up to as much, not tomorrow. Write `latest/notes/checked` if they changed or `checked` is older than a day. A box
   pinned to `vX.Y.Z` makes no API call (the release is the pin) and a box on a branch is never checked. Every number a
   parent-facing document quotes for this (`docs/privacy.md`, `docs/updating.md`, `docs/faq.md`) is pinned to the constants
   by `tests/test_docs.py`.
2. **Run** (on `update.request`, or `auto` inside the 03:00–05:00 window when `latest` is set and this version has
   not failed lately: a failure of the network or the disk (`transient`) is tried again after 30 minutes, any other
   failure waits 7 days): minimum 5 minutes between runs. Set `status=running, from, to, at`, clear the
   request, then start `sinko update --yes --from-panel` as a detached transient unit (`systemd-run --unit sinko-update
   --collect`; fall back to a detached subprocess). The runner survives the scheduler restart the installer causes.
3. **`sinko update`**: first require **more than 200 MB free** (`MIN_FREE_BYTES`, the installer's own number for an update, a
   rollback or a move from pihole-bahrain; a first installation needs more than 1 GB), before anything is downloaded and
   again after the downloads and the unpacked tree have used some of it: that failure is `transient`, says how much is free
   and that nothing was changed, and is `rolledBack` = `true` only after a self-check of the untouched box. Then download
   `sinko.tar.gz` + `.sha256` from the configured repo; verify the checksum; refuse any
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
5. `sinko rollback` re-installs the previous cached version by hand. `sinko update --ref vX.Y.Z` pins a release; the choice is
   saved only by an update that worked, and a failed `--ref` update (also after its rollback installer ran) leaves
   `SINKO_REF` as it was.
6. **Repair** (scheduler, a background look once a minute at `pb/version.txt`): an update cut off between the program's
   replacement and the page's swap leaves a program of one version and a page of another. When they have differed for
   `REPAIR_AFTER` (10 minutes) with nothing installing (no update lock, no installer lock, state not `running`), the box
   not removed on purpose, a believed clock and `/opt/sinko/src` holding this very version, the scheduler starts
   `sinko repair` as a unit of its own (the installer restarts the scheduler, so it cannot run inside it), at most once an hour
   (`repair.json`). `sinko repair` takes the update lock, requires the free space, runs the installer from `/opt/sinko/src`
   with the ref the box follows (nothing of Sinko is downloaded; the installer's `sinko setup` still runs `pihole -g`), checks
   the installed program and marks the cut-off update finished in the state (`ok`, or `rolledBack` true when it was the way
   back that was cut). A cut after the page swap, with page and program already agreeing, is not covered: the 3-minute check
   of step 4 reports it, and the scheduler's self-heal puts back missing groups and lists (once an hour).

`sinko selfcheck [--wait N]` (no DNS probing, exit ≠ 0 on failure, one line per check, waits up to N seconds for
"starting" to become "ok"): API reachable and logged in with the CLI password, the `pb-*` groups exist, every catalog list is
registered, **the scheduler has run: the same process up for at least 20 s, at least 2 finished passes of this version, a
heartbeat under 60 s old, and not crash-looping** (a unit that is merely "active" for a second between crashes does not
pass), every file `index.html` loads exists and is not empty (and every file the installer copies from the release's web
folder when the source is that version: the files directly in `web/` and in `web/fonts/`, no dotfiles; the check and
`install_files` must learn about a new web sub-folder together), and `pb/version.txt` equals `VERSION`. A check that cannot run
(an answer cut off by an FTL restart, say) is a failed check, and with `--wait` it is tried again.

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
  invalidated afterwards, so sign in again), `GET /api/info/system`, `/api/info/sensors`, `/api/info/host`,
  `GET /api/teleporter` (zip), `POST /api/teleporter` (multipart `file` + `import` JSON), `GET /api/dns/blocking`, and
  `GET /api/info/version` for **About** (the core's local version, read each time the sheet opens, shown as "Pi-hole v6.x"
  only when it is a plain release number; a failure or a development build leaves the row out).
* Backup = the whole Teleporter archive (it contains the password hash: the page says to keep it private).
  Restore imports **only** the gravity tables (`group, adlist, adlist_by_group, domainlist, domainlist_by_group,
  client, client_by_group`) and not `config`, so a restore never changes the address, upstreams or password.
* Update links: only `update.notes` values that `parseState` accepted are rendered as links.
* The page knows its own release from `<meta name="sinko-version">` (the installer stamps it; the placeholder stays in
  development and the page then goes by the box's `pb/version.txt`). A stamped page that finds the box on another version
  reloads itself once per version per tab (at start and when the phone returns to it, never while a dialog is open, a
  bedtime is typed or an update runs). The addresses of its scripts and style sheet carry `?v=<version>`.
* The counter line ("This box is one of N Sinko boxes online.") is shown from two boxes up, exactly two has its own wording,
  and the Arabic puts the number last. One tagline per language, from `tools/make-brand.py` (`TAGLINE_EN`, `TAGLINE_AR`);
  `tests/test_docs.py` and `tests/test_copy.py` compare the README, the site and the page with it.

## Ready-made image (Orange Pi Zero 3)

Golden-unit method: install Sinko on a fresh Armbian minimal (Debian Trixie) image, run `tools/seal.sh`, copy the SD
card to an `.img`, compress, flash many cards. Sealing removes everything that must be unique or private (SSH host keys,
machine-id, install-id, query history, parent password so the page offers the **claim screen**, shell history, logs),
locks the default root password / password SSH logins, removes Armbian's first-login wizard file, and arms
`sinko-firstboot.service`. First boot regenerates host keys, repairs the local name/address for the actual network
(`sinko configure`), refreshes `SINKO_IP`, then deletes the flag. `docs/product-image.md` is the seller's procedure.

The seal refuses a unit that is not ready (it names every problem; the account check is made again before the last step so
`--skip-checks` cannot bypass it): a service or timer not enabled, no avahi, automatic security updates not installed or not
switched on (unless `SINKO_OS_UPDATES=0`), no time service, a unit pinned to a version or following a branch (its boxes would
never be offered an update), a fixed address, another account than root that can log in on the console. Its watchdog drop-in
sets `RuntimeWatchdogSec=15` and `RebootWatchdogSec=15` (the Allwinner driver takes 16 s at most). In systemd 257 PID 1
disarms the watchdog before a power-off (`watchdog_timer` stays 0 for `poweroff` and `halt`, and `watchdog_setup(0)`
disarms) and keeps it armed for a reboot, where systemd-shutdown feeds it once per unmount pass and not during its first
sync; whether the board's driver lets it be disarmed (`CONFIG_WATCHDOG_NOWAYOUT`) only the board shows. The zero-fill counts
as done only on "No space left on device"; a leftover zero file is deleted by the next seal and by `firstboot.sh`.

## Things only real hardware can prove

Listed in `docs/hardware-test-checklist.md`; nothing in CI exercises: a real Pi-hole v6 (password config, teleporter,
sensors), systemd behaviour (`systemd-run`, reboot from the service), Armbian first boot, mDNS on phones, the
Orange Pi Zero 3's thermal sensor path, and a real GitHub release round trip. Also the things that depend on how slow a
real card and board are (the 20-second self-check, the restore window, the 15-second watchdog during a restart), on the
board's driver (Shut down and `CONFIG_WATCHDOG_NOWAYOUT`), on the real web server (the one-hour cache and the `?v=` addresses),
and on a cut of the power at the one moment between the program's replacement and the page's swap.

## Amendments agreed after the first review (these override the text above where they differ)

**State**: `update.rolledBack` is `true` (the previous version was put back and passes the self-check), `false` (the update
failed and going back did not work, or there was nothing to go back to, or the runner died) or `null` (not known / no
failure). Only the page decides what a parent reads from it: "the previous version is back" is shown only when it is
`true`. `update.error` is technical text for whoever helps (English, at most 200 characters, **never a command to type**);
the page does not print it as the reason in the parent's language. Both parsers are strict (ASCII digits, whole-string
regular expressions, no NaN/Infinity tokens, `timer.snapshot.services` must be an object, and an integer too big for a double
(more than 308 digits, which the page's JavaScript reads as Infinity) is refused by both, losing only its own field and never
the whole state); `tests/fixtures/state-cases.json` is the spec. The parsers drop top-level keys they do not know, so an older
component (after a rollback, or a phone that still holds the old page) wipes a field that a newer release added: that is
accepted, and it is why every key must be known to both sides in the release that introduces it.

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
