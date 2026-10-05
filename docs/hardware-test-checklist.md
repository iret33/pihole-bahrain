# Hardware test checklist (before the first public release and before every sale batch)

Everything below is exercised by the automated tests only against a **stand-in for Pi-hole and for the system**
(`tests/mock_pihole.py` and stubbed `systemctl`, `ip`, `pihole`...), or not at all. These steps need a **real Orange Pi
Zero 3 with a real Pi-hole v6, a real GitHub release and, for section G, the real GitHub and Cloudflare accounts**. Do them
once on a spare box, in this order, and write down the date and versions (board RAM, Armbian build, Pi-hole version,
Sinko version). A step that fails is a bug: open an issue.

Steps marked **[test unit]** need a shell on the box. That is fine on a unit you built for testing (a golden unit, or a
card sealed with `--keep-ssh-access`); a card sealed the way it ships has **no login**, so check it from the outside, or by
mounting the card on a computer.

## A. Fresh install (own hardware)

- [ ] Flash Armbian minimal (Debian Trixie), run the install one-liner from the latest release. It ends without errors and
      `sudo sinko doctor` shows only `ok`/`info`.
- [ ] `http://<ip>/` opens the page; the parent password works; `/admin/` (Pi-hole) still works with the same password.
- [ ] `http://<hostname>.local/` opens on an iPhone, a Mac or Windows PC, and on an Android phone (note which Android
      versions need the numeric address; the docs say "some").
- [ ] Router DNS pointed at the box: the page's checklist ticks "router" once a phone has used the box.
- [ ] Add a child's phone. Block YouTube: it stops in the app and in the browser. Allow it: it works again.
- [ ] Bedtime: set it two minutes ahead; internet goes off for the child's phone only, and returns at the end time.
- [ ] Reboot the box (power cut): everything comes back by itself, and the time is right within two minutes of the network.
      Bedtime and timers do not act on a clock that has not been set (they wait for the network time, for at most about ten minutes).
- [ ] Block list refresh: `sudo systemctl start sinko-lists` completes; rules still apply.
- [ ] **What the privacy text says about lookups is true.** On a new install `sudo pihole-FTL --config dns.upstreams` shows
      `1.1.1.3` and `1.0.0.3`, and `sudo pihole-FTL --config database.maxDBdays` shows `30`. On a box where Pi-hole was
      installed first, with its own settings, the installer leaves both as they were. With `sudo tcpdump -ni <interface> port 53`:
      a lookup for a name you blocked never appears on the wire towards the upstream; a lookup for a name you did not block
      does, in clear (this is what `docs/privacy.md` describes: a blocked name stays on the box, any other name goes to the
      upstream, together with the home's internet address).
- [ ] A quiet box: the live picture and the health card agree with what the child's phone is doing; `sudo systemctl status sinko`
      shows no restarts after an hour; Pi-hole's API seats do not fill up (the page's sign-in still works after the scheduler has
      been killed 20 times in a row with `sudo systemctl kill -s KILL sinko`; FTL has only 16 seats and a scheduler that did not
      give its session back would use them all).

## B. The parent page, on a real phone (iPhone Safari, Android Chrome), English and Arabic

- [ ] Everything fits, right-to-left looks right, nothing is cut off, buttons are easy to hit. Dark mode: the *Restart*, *Shut
      down* and *Restore* buttons and every other new control are easy to read.
- [ ] **My box**: health numbers look right (temperature near what `cat /sys/class/thermal/thermal_zone*/temp` says on a
      [test unit]). The Pi-hole version shown under *About* matches `pihole -v` on a [test unit].
- [ ] Scroll *My box* to its very end by touch on both phones: the sheet does not slide away or leave a blank page.
- [ ] *Change password*: the new password works, the old one does not, the scheduler keeps running (`systemctl status sinko`).
- [ ] *Backup* downloads a file; *Restore* of that file on a second box brings back devices, rules, timers, bedtime, and does
      **not** change that box's password or address. Restore a backup made **before** a migration or from a box that had
      other lists: the page says honestly what came back, and `sudo sinko doctor` is clean afterwards.
- [ ] *Restart* and *Shut down* do what they say, and the page recovers by itself after a restart. After *Shut down* the box
      **stays off** for ten minutes (the hardware watchdog does not bring it back).
- [ ] Unplug the box with the page open: within a minute or so the page says it cannot reach the box, and does not go on
      showing "Filtering: On" or "Last device seen: just now". Plug it in: the page recovers by itself.
- [ ] Add the page to the home screen: it opens like an app with the Sinko icon.
- [ ] *My box → Privacy* opens the privacy statement (`docs/privacy.md`), not the top of the README.

## C. Updates (needs two published releases, e.g. 3.0.0 and a test 3.0.1)

- [ ] On 3.0.0, *My box* shows "3.0.1 is available" within a day, or at once after *Check again*.
- [ ] *Update now*: progress shows, the page reloads at the end, `sinko --version` says 3.0.1, rules and devices are intact,
      filtering was down for no more than about a minute (watch a ping/DNS lookup from a phone during the update).
- [ ] *Restart* or *Shut down* pressed during an update: the box ignores it, the update finishes, and the box does not
      restart or power off later because of it.
- [ ] Unplug the internet and try again: a clear failure, nothing broken.
- [ ] Publish a deliberately broken 3.0.2 (installer exits 1, or fails the self-check): the box ends on 3.0.1, the page says
      the previous version is back (it is: `rolledBack`), and `sudo sinko doctor` is clean. Delete that release afterwards.
- [ ] Publish a 3.0.3 whose scheduler starts and then exits at once (a crash loop): the self-check notices, the box goes
      back to 3.0.1 and the page says so.
- [ ] A failed update where going back does not work (remove the cached copy of the previous version first,
      `/var/lib/sinko/cache`): the page does **not** say the previous version is back.
- [ ] **Pull the power during an update**, at five different moments (just after pressing the button, while it downloads, while
      it installs, right after, during the self-check). After every one the box starts; after about three minutes the page
      reports an honest ending ("worked", "failed, previous version back", or "failed, could not go back"), never "updating"
      for ever; no program, list, page or service file is empty (`sudo find /opt/sinko /var/www/html/pb /etc/systemd/system
      -size 0` finds nothing).
- [ ] `sudo sinko rollback` puts 3.0.0 back, offline.
- [ ] `sudo sinko update --ref v3.0.1` pins the box: the page then offers nothing newer, and `sinko update --check` says it is
      pinned. `sudo sinko update --ref latest` (with `--force` when already on the newest) follows releases again.
- [ ] Automatic updates: switch on, set the box clock to 03:10 (`timedatectl set-time` with NTP off), wait; it updates once.
- [ ] Check what the box sent to GitHub during an update: the release API and the two files `sinko.tar.gz` and
      `sinko.tar.gz.sha256`, and nothing else (watch `ss`/`tcpdump` or the proxy log). (`install.sh` is downloaded only by a
      new install.)

## D. Upgrading an old box

- [ ] On a box running pihole-bahrain 2.2.0 (devices, bedtime, a timer running): `sudo pihole-bahrain update`. It ends on
      Sinko with devices, rules, bedtime, hostname and password intact; the old services and folders are gone;
      `pihole-bahrain doctor` still works (compatibility command); the lists in Pi-hole's admin still point to a working address.
- [ ] After migrating, *My box* shows "up to date" (or the newer version) after *Check again*, and `sudo sinko update --check`
      does **not** say "developer branch": a 2.x box that had the old default `master` follows releases now.
- [ ] While the migration runs, a name you blocked stays blocked (ask for it every second from a phone or with `dig`): there is
      no moment without filtering.
- [ ] Pull the power during a migration (before the new files, after them, while the lists are registered): after power-up
      exactly one scheduler runs, filtering is on, and running the installer again finishes the job.
- [ ] A 2.x box that kept its lists in the old program folder (a local lists address): it works after the old folder is gone.
- [ ] A 2.x box with its own "block everything" rule (an allow-list-only Pi-hole): the rule keeps working after the migration,
      and it still lets that rule's own allowed domains through while *internet off* or *pause* is on.

## E. The ready-made image

Check the flashed card from the outside (browser, router, `ssh` from another computer) and by mounting the card on a
computer. Only a unit built for testing has a shell.

- [ ] Build a golden unit, seal it, image it, flash a **second** card, start it ([`product-image.md`](product-image.md)): the
      welcome screen appears, SSH host keys differ from the golden unit's, the machine id differs, no leftover devices,
      history or logs, the box's address and `family.lan` are right even though the network differs from where it was built.
- [ ] **No way in.** `ssh -v root@<box>` with the throw-away password is refused and does not offer `password` (only
      `publickey`, or nothing); with a screen and keyboard on the HDMI port the console shows a **login prompt**, not a root
      shell, and the throw-away password does not log in (check that Armbian's automatic root login really is gone from the
      image).
- [ ] **Mount the flashed card on a computer** and look: no Wi-Fi profile or password of the golden unit's network
      (`/etc/netplan`, `/etc/NetworkManager/system-connections`), no rotated Pi-hole logs in `/var/log` or `/var/log.hdd`,
      no `/root/.bash_history`, no old journal directories under the golden unit's machine id, no key in `/root/.ssh`. If
      `/etc/pihole/config_backups` or `gravity_backups` still hold the golden unit's old settings or test devices, say so in
      the issue.
- [ ] **Each box has its own HTTPS key.** The certificate of Pi-hole's HTTPS port (`openssl s_client -connect <box>:443
      </dev/null | openssl x509 -noout -fingerprint`) is different on two cards flashed from the same image.
- [ ] **No leftover login of the golden unit's.** On a spare golden unit, create a Pi-hole application password and switch on
      two-factor sign-in, seal, flash a card and claim it: the new password signs in without a code, and the old application
      password does not open the API.
- [ ] Flash the same image on a **larger** card and write down what happens to the root filesystem (`lsblk`, `df -h /`).
      [`product-image.md`](product-image.md) says it keeps the image's size unless the seal re-arms Armbian's resize: if it
      does grow, update the text.
- [ ] Start the unit with **no cable**, plug the cable in a minute later: the box ends reachable at `http://sinko.local` and
      at its address, with the right name for the router's DNS setting.
- [ ] The first-start service and SSH: `journalctl -b | grep -i "ordering cycle"` finds nothing, `systemctl status
      sinko-firstboot ssh` [test unit] look right, and on a box where SSH is socket-activated the host keys still get made.
- [ ] `sinko.local` answers on an iPhone, a Mac and an Android phone from the very first start (Avahi is installed and
      enabled on the shipped image).
- [ ] Pull the power five times during normal use: no corruption; the box starts every time.
- [ ] Leave it running for 72 hours with a few phones: no memory growth (`free -m`), temperature acceptable in the case,
      SD card writes modest (`cat /sys/block/mmcblk0/stat`, compare before and after).
- [ ] The hardware watchdog is armed (`systemctl show -p RuntimeWatchdogUSec`) and `journalctl -b | grep -i watchdog` shows no
      complaint about the timeout; optional stress test: `echo c | sudo tee /proc/sysrq-trigger` (only on a spare box)
      reboots it by itself.
- [ ] While the box is unclaimed (the welcome screen is showing), another phone that opens the page also sees the welcome
      screen; after the first one chooses a password, the second sees the sign-in. Test units are not left running on a
      shared network.

## F. The counter (only when you deployed it)

- [ ] On a box, answer *Yes* in the page: within a few minutes the service's `/v1/stats` shows `online` going up by one.
      Answer *No*: **within a minute the row is gone** from the database
      (`npx wrangler d1 execute sinko-counter --remote --command "SELECT COUNT(*) FROM installs"` is one lower, and
      `/v1/stats` follows), nothing more is sent, and on a [test unit] `/var/lib/sinko/install-id` has been deleted.
- [ ] Switch the counter off while the box has no internet: the box keeps its code and sends no pings, and about six hours
      later, with internet back, the record is deleted. The same with an older Worker that answers `404` to
      `/v1/forget`: the box keeps asking and sends nothing else.
- [ ] The box's IP address and the family's names appear nowhere in the service's data, and Workers Logs shows no request log
      (`wrangler.toml` switches them off).
- [ ] Cloudflare's current D1 "Time Travel" retention matches what `docs/privacy.md` says ("up to about a month"); fix the
      text if it does not. The free-plan limits quoted in `telemetry/README.md` are still right.
- [ ] On `workers.dev`, the Cache API does nothing and the counts still work from the database; with a custom domain, a
      rate-limiting rule for `/v1/ping` and `/v1/forget` can be created.

## G. The project itself (needs the real GitHub repository)

- [ ] **A release round trip.** Tag a test release: the *Release* workflow's two jobs (read-only build, write publish) both
      succeed and the release has `sinko.tar.gz`, `sinko.tar.gz.sha256`, `install.sh` and the notes. This is the first time
      that workflow runs; nothing here could run it.
- [ ] With `## [X.Y.Z] - Unreleased` in `CHANGELOG.md`, tagging stops at the changelog step and publishes nothing.
- [ ] The one-liner from the new release installs on a clean box; a box on the previous version updates from the page.
- [ ] The repository's own protections are really on (they are settings, not files): two-factor authentication or passkeys, a
      second owner, a tag ruleset for `v*`, a protected `master` with code-owner review, Dependabot on.
- [ ] The Pages site is live. Before the screenshots are in `docs/img/` it shows no gaps (no empty frames, no "See it"
      heading); after they are, they appear. The numbers strip shows real numbers; with the counter off, only downloads.
- [ ] The old `iret33/pihole-bahrain` address still redirects (web, git, `raw.githubusercontent.com`) after the rename, and a
      2.x box updates through it.
- [ ] `TELEMETRY_URL` in `bin/sinko` and `statsUrl` in `site/config.js` are set to the deployed counter **before** the tag, and
      that counter has `/v1/forget`.
