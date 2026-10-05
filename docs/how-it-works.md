# How Sinko works

*For people who want to understand, check or change it. Parents do not need any of this.*

Contents: [The pieces](#the-pieces) · [The live picture](#the-live-picture) · [Block lists](#block-lists) · [Files on the box](#files-on-the-box) · [Going deeper](#going-deeper)

## The pieces

```
Parent's phone ──► http://box/  (index.html + /pb/app.js)
                        │  Pi-hole REST API (/api), signed in with the parent password
                        ▼
                  Pi-hole FTL  ◄──── sinko.service (timers, bedtime)
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
  disabled group `pb-state`. The page writes them; `sinko run`
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

Everything Sinko creates in Pi-hole starts with `pb-` or has a comment starting with `pb:`, and your own groups, lists, clients
and rules are never touched. (`pb-` is an internal prefix kept from earlier versions; it is not the product name.) One
exception follows from how Pi-hole works: it allows only one deny rule per pattern. If you already have your own `^.*$`
rule, Sinko adds its two groups to it and leaves everything else about it alone, and never deletes it; while "internet
off" or "pause" is on, whatever your own rule allows still resolves.

## The live picture

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
  block lists, `sinko domain-map`); anything else is "A website". Raw domain names are never shown.
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
`sudo sinko update`.

To add a service: create `lists/<id>.txt`, add an entry to `services.json`
(id, English and Arabic names, category, colour, whether Homework mode blocks
it), and run the tests. The tests refuse lists that would block shared
infrastructure such as `google.com`, `apple.com` or `akamaihd.net`.

The lists are public domain (CC0), so you may subscribe any Pi-hole to them directly. A box downloads them from
`SINKO_LISTS_BASE` (by default this repository's `lists/` on GitHub, branch `master`), which is separate from the release a
box runs: list fixes reach boxes without a new release, and a box can be pointed at your own copy.

## Files on the box

| What | Where |
|---|---|
| Program, lists, tools, the last downloaded release | `/opt/sinko` (`bin/sinko`, `lists/`, `tools/`, `src/`, `LICENSE`, `NOTICE`) |
| Settings | `/etc/sinko/config` (`KEY=value`, read by the installer and the program) |
| Runtime data (root only) | `/var/lib/sinko/` (counter id, handled requests, cached releases for rollback, update results) |
| The parent page | `/var/www/html/index.html` and `/var/www/html/pb/` (includes `pb/box.json`, written by the program) |
| Services | `sinko.service` (scheduler), `sinko-lists.timer` (nightly list refresh), `sinko-firstboot.service` (ready-made boxes only) |
| Install log | `/var/log/sinko-install.log` (never contains a password) |

## Going deeper

* [`docs/maintainers/architecture.md`](maintainers/architecture.md): the contracts between the parts (state, updates,
  release assets, the counter).
* [`docs/updating.md`](updating.md): how updates work and how they are protected.
* [`docs/privacy.md`](privacy.md): everything that is sent anywhere.
* [`CONTRIBUTING.md`](../CONTRIBUTING.md): tests and how to help.
