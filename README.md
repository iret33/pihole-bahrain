<p align="center">
  <img src="docs/img/wordmark-en.svg" alt="Sinko" height="72">
</p>

<p align="center"><strong>Calm internet for the family.</strong><br>
Parental controls that live on a small box in your home, in Arabic and English.<br>
<a href="https://iret33.github.io/sinko/">Website</a> · <a href="docs/install.md">Install</a> · <a href="docs/faq.md">FAQ</a> · <a href="docs/privacy.md">Privacy · الخصوصية</a></p>

<p align="center">
  <a href="https://github.com/iret33/sinko/releases"><img alt="Downloads" src="https://img.shields.io/github/downloads/iret33/sinko/total?label=downloads&color=0F766E"></a>
  <a href="https://github.com/iret33/sinko/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/iret33/sinko?color=0F766E"></a>
  <a href="https://github.com/iret33/sinko/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/iret33/sinko/actions/workflows/ci.yml/badge.svg"></a>
  <a href="COPYING.md"><img alt="Licence: GPL-3.0-or-later" src="https://img.shields.io/badge/licence-GPL--3.0--or--later-0F766E"></a>
</p>
<!-- Add this badge once the counter service is deployed (telemetry/README.md), with your own address:
<a href="docs/privacy.md"><img alt="Boxes online" src="https://img.shields.io/endpoint?url=https://YOUR-COUNTER/badge/online.json"></a> -->

<p align="center">
  <img src="docs/img/panel-en.png" alt="The parent page: Children are online, Turn internet off, the live picture" width="250">
  &nbsp;
  <img src="docs/img/panel-ar.png" alt="The same page in Arabic, right to left" width="250">
  &nbsp;
  <img src="docs/img/box-en.png" alt="My box: updates, health, addresses" width="250">
</p>

Sinko is a simple page for parents, on top of [Pi-hole](https://pi-hole.net) v6. Every phone, tablet and console at home
asks a small box in your house "where is this app?" first, and the box says yes or no. From your phone you can:

- **block or allow apps** (YouTube, TikTok, Roblox, Instagram, Snapchat, …) with one tap;
- switch on **Homework** mode, a timed **Free time**, or a timed **Offline break**;
- **pause** one child's device;
- set a **Bedtime** that turns the internet off at night and back on in the morning;
- watch **the live picture**: what the box is checking and stopping, in words a parent can follow;
- look after the box from the same page (**My box**): update it, see its health, change the password, back up, restart.

Timers and bedtime run on the box itself, so they keep working after you close the page. Nothing needs a computer
after the first set-up.

## Two ways to get Sinko

**Buy it ready-made.** An Orange Pi Zero 3 with Sinko already on its card: plug in the cable and the power, open
`http://sinko.local` on your phone, choose a password, point your router at the box. See the
[project website](https://iret33.github.io/sinko/) for where to buy.

**Build your own, free.** Any small Debian-based computer will do (an Orange Pi Zero 3 or a Raspberry Pi is ideal). One command:

```bash
curl --proto '=https' --proto-redir '=https' -fsSL https://github.com/iret33/sinko/releases/latest/download/install.sh | sudo bash
```

It installs Pi-hole v6 if needed, Sinko, the services and the block lists, asks you for a parent password, and tells you the
address of the page and the one setting to change in your router. Running it again updates Sinko and keeps your password,
devices and rules. Everything about it (options, the Orange Pi Zero 3 steps, keeping the box safe) is in
[`docs/install.md`](docs/install.md).

Sinko is free software (GPL-3.0-or-later). Anyone may build, share and sell help with it; flashed boxes sold under the
Sinko name are covered by [`TRADEMARK.md`](TRADEMARK.md), and [`docs/selling.md`](docs/selling.md) explains what a seller owes
the software's authors.

## Updates

Open **My box**: it says when a newer version exists, and **Update now** installs it, checks that it works and
**puts the previous version back by itself if it does not**. An update never touches your password, devices, rules,
timers or bedtime. There is an option to update automatically at night. Details and the protections:
[`docs/updating.md`](docs/updating.md).

## Privacy, plainly

Sinko keeps your children's names and devices, your rules and your settings on the box, and never sends them to the project
or to anyone else. There is no account and no tracking. Two things you should know:

- Like every DNS filter, Pi-hole passes on the **names of sites it cannot answer itself** to an upstream DNS service
  (Cloudflare for Families, by default, on a box Sinko sets up). It sees those names and your home's internet address. The
  project never receives them. You can choose another service in Pi-hole's settings.
- An optional **anonymous counter** (how many Sinko boxes are online) is **off until you say yes**; it sends a random number,
  the version and the kind of board, and nothing else. Switching it off makes the box ask the counter to forget it.

The whole statement, in English and Arabic, with exactly what is sent and when: [`docs/privacy.md`](docs/privacy.md).

## What it cannot do

DNS filtering is a strong everyday filter, not a lock:

- A device on **mobile data**, or using a **VPN** app, does not use the box.
- Apps that connect to fixed IP addresses (Telegram does this in part) may keep working after being blocked.
- Apps that are already open can take a few minutes to stop, until the device's own DNS cache expires.
- A child who knows the main Wi-Fi password and can change network settings can get around it.

Use it together with Screen Time (iPhone) or Family Link (Android). More in the [FAQ](docs/faq.md).

## Documentation

| | |
|---|---|
| [`docs/install.md`](docs/install.md) | Install, options, Orange Pi Zero 3, router step, commands, removing |
| [`docs/updating.md`](docs/updating.md) | Updates, rollback, pinning a version |
| [`docs/troubleshooting.md`](docs/troubleshooting.md) | An app still works? Cannot open the page? Forgot the password? |
| [`docs/faq.md`](docs/faq.md) | Questions and answers |
| [`docs/privacy.md`](docs/privacy.md) | What is sent, where, and how to turn it off (English and Arabic) |
| [`docs/how-it-works.md`](docs/how-it-works.md) | Pi-hole groups, the live picture, the block lists, files on the box |
| [`docs/product-image.md`](docs/product-image.md), [`docs/selling.md`](docs/selling.md) | Making and selling ready-made boxes |
| [`docs/hardware-test-checklist.md`](docs/hardware-test-checklist.md) | What to test on a real box before a release or a sale |
| [`docs/maintainers/`](docs/maintainers) | Architecture and contracts; releasing |
| [`CONTRIBUTING.md`](CONTRIBUTING.md), [`SECURITY.md`](SECURITY.md), [`CHANGELOG.md`](CHANGELOG.md) | Helping, reporting a vulnerability, what changed |

## Development

```bash
python3 -m unittest discover -s tests -p "test_*.py"    # box program, updater, lists, doctor, diagnose, copy, release build
node --test tests/*.test.js                              # the page's logic (state, live picture, My box)
sudo bash tests/run_shell_tests.sh                       # installer, migration, seal, first start (stubbed system)
python3 tests/ui_smoke.py --shots /tmp/shots             # the page in a real browser, English and Arabic (needs playwright)
python3 tests/mock_pihole.py --web web --setup --live    # the page at http://127.0.0.1:8080, password "test", with demo traffic
```

`tests/mock_pihole.py` imitates the parts of the Pi-hole v6 API Sinko uses, so no device is needed. Full list and the
pull-request checklist: [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Licences and names

- Sinko's code is **GPL-3.0-or-later** ([`LICENSE`](LICENSE), [`COPYING.md`](COPYING.md)); the block lists in `lists/` are
  public domain (CC0); the bundled font, IBM Plex Sans Arabic, is under the SIL Open Font License (`web/fonts/OFL.txt`).
- "Sinko" and its logo are the project's marks ([`TRADEMARK.md`](TRADEMARK.md)). Third-party software and trademarks are
  listed in [`NOTICE`](NOTICE).
- Pi-hole is a registered trademark of Pi-hole LLC and is licensed under the EUPL-1.2. Sinko is independent software that
  works with it, and is not affiliated with, endorsed by or supported by Pi-hole LLC.
