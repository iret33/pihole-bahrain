# Nay

A simple parental-controls page on top of [Pi-hole](https://pi-hole.net) v6,
in Arabic and English. Parents can:

- block or allow apps (YouTube, TikTok, Roblox, …) with one tap;
- switch on **Homework** mode, a timed **Free time**, or a timed **Offline break**;
- pause a single child's device;
- set a **Bedtime** schedule that turns the internet off at night;
- watch **the live picture**: every phone and tablet at home asks the family
  box first, and the box says yes or no. Requests travel along wires as little
  packets, and the page shows how many were checked and stopped in the last 24 hours.

Timers and bedtime run on the box itself, so they keep working after the
parent closes the page.

## Install

On a fresh Debian-based device (Armbian, Debian 12/13, Ubuntu 22.04+,
Raspberry Pi OS) connected by Ethernet:

```bash
curl -fsSL https://raw.githubusercontent.com/iret33/nay/master/install.sh | sudo bash
```

The installer:

1. installs Pi-hole v6 if it is missing (fully unattended);
2. asks for a parent password. If it cannot ask, it generates one and shows it on the screen only; with no screen at
   all it saves it in `/etc/nay/initial-password` (readable by root only: read it, then delete the file).
   The password is never written to the install log;
3. installs the parent page at `http://<box-ip>/` (the Pi-hole admin stays at `/admin/`);
4. creates the Pi-hole groups, rules and block lists;
5. starts the scheduler service and a nightly list refresh;
6. prints the address, and the router step below.

Running the same command again updates everything and keeps the password,
devices and rules.

An existing Pi-hole v6 is used as it is. If the device still runs Pi-hole v5,
update it first with `sudo pihole -up` (the installer stops and says so).

### Options

Pass these as environment variables after `sudo`, for example
`curl … | sudo NAY_PASSWORD='s3cret-pass' bash`.

| Variable | Default | Meaning |
|---|---|---|
| `NAY_PASSWORD` | ask / generate | Parent password (also the Pi-hole admin password) |
| `NAY_HOSTNAME` | `family.lan` | Local name for the page; `none` to skip |
| `NAY_UPSTREAMS` | `1.1.1.3,1.0.0.3` | Upstream DNS for a new Pi-hole (Cloudflare for Families: also blocks malware and adult sites) |
| `NAY_TIMEZONE` | `Asia/Bahrain` if the system is on UTC | Time zone used by bedtime |
| `NAY_LISTS_BASE` | this repo's `lists/` on GitHub | Where Pi-hole downloads the service lists |
| `NAY_REPO`, `NAY_REF` | this repo, `master` | Source to install from (use a tag for pinned releases) |
| `NAY_NONINTERACTIVE` | – | `1` = never prompt |

### Last step: point the home network at the box

Pi-hole only filters devices that use it for DNS. In the router settings:

1. set the **DNS server** (LAN/DHCP settings) to the box's address, and only that address;
2. **reserve** that address for the box (DHCP reservation / static lease);
3. turn Wi-Fi off and on on the children's devices, then add them on the page.

If the router also hands out IPv6 DNS servers, turn that off (or set it to the
box too), otherwise devices can skip the filter over IPv6.

## Preparing an Orange Pi Zero 3

1. Flash **Armbian minimal, Debian Trixie** for Orange Pi Zero 3 from
   <https://www.armbian.com/orange-pi-zero-3/> onto a high-endurance microSD card
   (8 GB or more).
2. Connect Ethernet and power. Log in over SSH (`root` / `1234`) and finish
   Armbian's first-login questions. **Choose a strong root password there** (see
   [Keep the box itself safe](#keep-the-box-itself-safe)).
3. Run the install command above.

Notes: avoid the 1.5 GB RAM model (current kernels crash on it); use the
Ethernet port rather than Wi-Fi; the installer keeps Pi-hole's query history to
30 days to reduce SD-card wear.

### Keep the box itself safe

The box is on the same network as the children's devices. A child who can log in
to it as `root` can take their own device out of the rules or switch the filter
off, so:

- change the default password at once (`passwd`) to something long that only you
  know; never leave it at `1234`, and avoid short or numeric ones;
- better, allow SSH keys only. From your computer run `ssh-copy-id root@<box-ip>`,
  then **test that the key login works in a second window**, and only then on the box:

  ```bash
  echo 'PasswordAuthentication no' | sudo tee /etc/ssh/sshd_config.d/00-keys-only.conf
  sudo systemctl restart ssh
  ```

- the parent password (also the Pi-hole admin password) is a separate secret; keep
  it from the children as well.

## How it works

```
Parent's phone ──► http://box/  (index.html + /pb/app.js)
                        │  Pi-hole REST API (/api), signed in with the parent password
                        ▼
                  Pi-hole FTL  ◄──── nay.service (timers, bedtime)
                        │
Child's device ──DNS──► │  blocked if the device is in an enabled pb-* group
```

- Every service has its own Pi-hole group `pb-svc-<id>` that owns one block
  list. Lists are **always enabled** (Pi-hole leaves disabled lists out when it
  rebuilds), and a service is blocked by **enabling its group**. Changes apply
  immediately — no DNS restart.
- Child devices are clients that belong to `pb-kids`, `pb-guard` (blocks
  outside DNS-over-HTTPS resolvers), `pb-offline` and every `pb-svc-*` group.
  Pausing a device adds it to `pb-paused`.
- `pb-offline` and `pb-paused` share one "block everything" rule, so
  "internet off" only affects children's devices, never the parents'.
- Timer and bedtime settings are stored as JSON in the description of the
  disabled group `pb-state`. The page writes them; `nay run`
  (a systemd service) enforces them every 15 seconds.
- Devices are added by MAC address when Pi-hole knows it (stable across IP
  changes), otherwise by IP address. An IPv6-only entry is not offered, because
  phones rotate their IPv6 privacy addresses every day.
- **Which rule applies to a device.** Pi-hole takes a device's groups from the
  first of these that matches: a client row for its IP address or a subnet (the
  longest match wins), then its MAC address, then its host name. So an IP or
  subnet row, for example `192.168.1.0/24` added in the Pi-hole admin, silently
  overrides a child's MAC row, and none of the child's rules apply. `doctor`,
  `status` (`shadowed_by`) and the page warn about this. Remove such a row, or give
  a row that covers only that one device the same groups. Matching by MAC also needs
  Pi-hole's `resolver.macNames` setting to stay on (it is on by default).

Everything the add-on creates in Pi-hole starts with `pb-` or has a comment
starting with `pb:`, and your own groups, lists and rules are never touched.

### The live picture

The card under the big button draws what Pi-hole is doing, in words a parent can follow:
devices on top, the family box in the middle, the internet at the bottom. A request
goes to the box; a stopped one comes back as a dead end (a red packet with a cross), an
allowed one goes on to the internet and back (a teal packet with a tick), and one the box
remembered turns around at the box. A dotted line around the box shows that after a yes the
device connects by itself: videos and messages never pass through the box.
*Show me how it works* plays a five-step tour with clearly labelled **Example** packets from
an example phone; examples never change a number. *More detail* explains the four steps,
shows busy hours, the apps stopped most, and explains the technical words (DNS, gravity, cache).

What it reads, and how often: `/api/stats/summary` every 15 s (the three numbers: checked,
stopped, share; *whole home, last 24 hours*), `/api/queries` every 3.5 s with a small `length` and
`from` taken from the box's own clock, and `/api/history` plus `/api/stats/top_domains` only while the
detail sheet is open. Nothing is read while the page is hidden, a dialog is open, or the picture is folded
away (the chevron remembers its state). At most about 3 packets a second are drawn; the rest are only counted.
The picture never changes a rule.

- **Names.** An app is named only when its domain is in `pb/domains.json` (made at install time from the
  block lists, `nay domain-map`); anything else is "A website". Raw domain names are never shown.
  A *stop* is credited to an app only when that app is blocked for the child it came from: an ad list also
  stops trackers on an allowed app's domain, and that reads "An unwanted site", never "Stopped Netflix". While the
  internet is off or a device is paused, a stop says that instead. A device is named for a stop only, never for an allowed lookup.
- **Each child's wire** shows what is true: *Online now*, *Quiet*, *Paused*, *Internet off*, *Rules may
  not apply* (another Pi-hole client row overrides the child's, see above), *Not seen in 24 hours*
  (the device is probably not using the box) and *Rules set* (the box cannot tell when it was last online, for
  example at a privacy level that hides devices: never a guess like "not seen"). The headline says "the rules are working"
  only when nothing needs a look and a device was seen lately; if every device is quiet, or one has been quiet for
  hours (a phone that left Wi-Fi looks exactly like that), it says "the rules are set". If Pi-hole's blocking is switched off
  (`/api/dns/blocking`), the card says so in amber instead of anything green.
- **Privacy levels.** At level 1 packets are drawn without app names, at level 2 clients are folded into
  "Whole home", at level 3 only the totals are shown, with a note saying why.
- **Reduced motion.** Nothing moves; the "What just happened" list opens by default and a short summary is
  announced to screen readers at most every 30 seconds.
- Arabic is drawn right to left with the whole picture mirrored; digits stay Latin.

## Block lists

`lists/` holds one list per service (Adblock style, `||domain^` per line) plus
`guard.txt`. `lists/services.json` is the catalog shown on the page.

Pi-hole downloads the lists straight from this repository, so a change pushed
to `master` reaches every box on its next nightly refresh (or immediately with
`sudo pihole -g`). Adding a **new** service also needs the new code on the box:
`sudo nay update`.

To add a service: create `lists/<id>.txt`, add an entry to `services.json`
(id, English and Arabic names, category, colour, whether Homework mode blocks
it), and run the tests. The tests refuse lists that would block shared
infrastructure such as `google.com`, `apple.com` or `akamaihd.net`.

## Commands on the box

```bash
sudo nay doctor     # check the installation, and that DNS really reaches the box
sudo nay diagnose   # why is a blocked app still working? (read-only report)
sudo nay watch      # does a device's DNS reach the box? (turn its Wi-Fi off/on, open the app)
sudo nay use-mac    # re-register children added by IP address under their MAC address
sudo nay status     # current rules, devices, timer, bedtime (JSON)
sudo nay update     # update to the latest version
sudo nay setup      # re-create groups/lists if something was deleted
sudo pihole setpassword        # change the parent password
journalctl -u nay   # scheduler log
sudo /opt/nay/uninstall.sh   # remove the add-on (Pi-hole stays)
```

Files: code in `/opt/nay`, settings in `/etc/nay/config`,
page in `/var/www/html/index.html` and `/var/www/html/pb/`, install log in
`/var/log/nay-install.log`.

## Troubleshooting

**An app still works after it was blocked.** Go through these in order:

1. `sudo nay doctor`. Every line should say `ok` or `info`. A `FIX` line
   says what is wrong and what to do; a `WARN` line is advice (for example that a
   child's device has sent no DNS query for a day, or that the network uses IPv6).
2. `sudo nay diagnose`. A read-only report that ends with the likely
   causes, most likely first. It tests YouTube and Instagram; name other apps with
   `diagnose tiktok roblox`.
3. `sudo nay watch`. Turn Wi-Fi off and on on the child's device, then open
   the app, or `m.youtube.com` in its **browser** (apps remember answers for a while).
   If no line appears, the device does not use the box for DNS. If it says "answered
   normally", no child rule matches that address.

The usual causes:

- **The device was not added** on the page. Rules apply only to added devices.
- **DNS never reaches the box.** The router relays DNS for everyone (then every query
  looks like it comes from the router), the router hands out its own IPv6 DNS server,
  or the phone uses Private DNS, a VPN or mobile data.
- **Another client row overrides the child's** (see "Which rule applies to a device").
- **The child was added by IP address** and the address changed. Run
  `sudo nay use-mac` to re-register it by MAC.

**Searching the lists by hand.** Pi-hole stores these Adblock-style entries as
`||youtube.com^`, not `youtube.com`, so searching for the bare domain finds nothing
even though it is blocked. Search for the stored form:

```bash
sudo pihole-FTL sqlite3 -readonly /etc/pihole/gravity.db "SELECT adlist_id, domain FROM gravity WHERE domain = '||youtube.com^'"
```

## What it cannot do

DNS filtering is a strong everyday filter, not a lock:

- A device on **mobile data**, or using a **VPN** app, does not use the box.
- Apps that connect to fixed IP addresses (Telegram does this in part) may keep
  working after being blocked.
- Apps that are already open can take a few minutes to stop, until the device's
  own DNS cache expires.
- A child who knows the main Wi-Fi password and can change network settings can
  get around it. Pair it with Screen Time (iPhone) or Family Link (Android).

## Development

```bash
python3 -m unittest discover -s tests -p "test_*.py"   # core, schedule, lists, doctor, diagnose
sudo bash tests/test_install.sh                          # installer, stubbed system
node --test tests/pb-core.test.js                        # the live picture's logic (feed, pacing, states, curves)
python3 tests/ui_smoke.py --shots /tmp/shots            # browser test (needs playwright)
python3 tests/stage_smoke.py                             # drawing engine on a fixture page
python3 tests/live_smoke.py --shots /tmp/shots          # the live picture on the real page: numbers, packets, tour, Arabic, privacy
python3 tests/mock_pihole.py --web web --setup --live    # page on http://127.0.0.1:8080, password "test", with demo traffic
```

`tests/mock_pihole.py` imitates the parts of the Pi-hole v6 API this project
uses, so the page can be developed without a device.

## Licences and names

- Pi-hole is licensed under the EUPL-1.2 and "Pi-hole" is a registered
  trademark; check the
  [Pi-hole trademark rules](https://pi-hole.net/trademark-rules-and-brand-guidelines/)
  before using the name in a product.
- The bundled font, IBM Plex Sans Arabic, is under the SIL Open Font License
  (`web/fonts/OFL.txt`).
- This repository has no licence file yet, which means all rights are reserved.
  Add one before accepting outside contributions.
