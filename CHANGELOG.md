# Changelog

All notable changes to Sinko. Versions follow [Semantic Versioning](https://semver.org); the newest release is at the top.
Boxes learn about a release from this repository's GitHub releases, whose notes are the section of this file.

## [3.0.0] - Unreleased

Sinko 3.0 is the first public release: the project has a new name, a parent page that looks after the box by itself, and
everything needed to ship it on a ready-made Orange Pi Zero 3.

### Changed
- **New name: Sinko** (was pihole-bahrain, then "Family Internet"). The command is `sinko`, the code lives in
  `/opt/sinko`, settings in `/etc/sinko/config`, the services are `sinko.service` and `sinko-lists.*`, and the
  installer variables start with `SINKO_` instead of `PB_`. Pi-hole groups, lists and rules keep their internal `pb-`
  prefix, so nothing already set up is lost.
- Installing and updating now use a **checksum-verified release archive** from GitHub Releases instead of cloning the
  repository; `SINKO_REF=master` still installs a branch for developers.

### Added
- **My box** in the parent page: update (check, update now, automatic at night), box health, addresses, change the
  parent password, backup and restore, restart and shut down, the anonymous counter question, and About.
- **Updates from the page**, with checksum verification, a cached copy of the previous version, an automatic self-check
  and automatic rollback if the new version does not work. `sudo sinko update`, `sinko rollback`, `sinko selfcheck`.
- **First-run set-up** for ready-made boxes: a welcome screen to choose the parent password, and a short checklist
  (router, first device, counter question).
- The box **keeps its local name working** when the router gives it a new address, and is reachable as
  `<hostname>.local` (Avahi).
- An **optional, anonymous "boxes online" counter**, off until the parent says yes (see `docs/privacy.md`), a download
  count from GitHub Releases, and a project website.
- `tools/seal.sh` and `tools/firstboot.sh` to prepare and personalise the ready-made image (`docs/product-image.md`).
- Debian security updates are switched on by default (`SINKO_OS_UPDATES=0` to skip), and bedtime and timers wait for the
  clock to be set on boards without a real-time clock.
- GPL-3.0-or-later licence (lists: CC0), security policy, contribution guide, code of conduct, issue templates.

### Migrating from pihole-bahrain 2.x
Run `sudo pihole-bahrain update` (or the new install command). The installer moves your settings, keeps your devices,
rules and password, replaces the old services and leaves a `pihole-bahrain` command that points to `sinko`.

## [2.2.0] - 2026-10-03
### Added
- **The live picture**: a card that draws, in plain words, what the family box is doing: devices on top, the box in the
  middle, the internet below, with the numbers checked and stopped in the last 24 hours, a guided tour and a detail
  sheet. Arabic is drawn right to left. It never changes a rule.
- The page follows the box's clock for timers and "last online".

## [2.1.0] - 2026-10-03
### Added
- `diagnose` and `watch` commands to find out why a blocked app still works; `use-mac` to re-register children added by IP.
- `doctor` checks that DNS really reaches the box for each child device.
- Troubleshooting and hardening advice in the README.
### Changed
- Device identities use the MAC address, never a rotating IPv6 address; the generated parent password never reaches the
  install log; IP or subnet client rows that override a child's rules are detected and reported.

## [2.0.1] - 2026-09-25
### Fixed
- Installer on an existing Pi-hole v6, stale host names, failed downloads.

## [2.0.0] - 2026-09-17
### Changed
- Rewritten as a standalone add-on for Pi-hole v6: one Pi-hole group and list per service, timers and bedtime enforced by a
  service on the box, parent page served at `/`.
