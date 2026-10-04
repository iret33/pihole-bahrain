# Hardware test checklist (before the first public release and before every sale batch)

Everything below is exercised by the automated tests only against a **stand-in for Pi-hole and for the system**
(`tests/mock_pihole.py` and stubbed `systemctl`, `ip`, `pihole`...). These steps need a **real Orange Pi Zero 3 with a real
Pi-hole v6 and a real GitHub release**. Do them once on a spare box, in this order, and write down the date and versions
(board RAM, Armbian build, Pi-hole version, Sinko version). A step that fails is a bug: open an issue.

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
- [ ] Block list refresh: `sudo systemctl start sinko-lists` completes; rules still apply.

## B. The parent page, on a real phone (iPhone Safari, Android Chrome), English and Arabic

- [ ] Everything fits, right-to-left looks right, nothing is cut off, buttons are easy to hit.
- [ ] **My box**: health numbers look right (temperature near what `cat /sys/class/thermal/thermal_zone*/temp` says).
- [ ] *Change password*: the new password works, the old one does not, the scheduler keeps running (`systemctl status sinko`).
- [ ] *Backup* downloads a file; *Restore* of that file on a second box brings back devices, rules, timers, bedtime, and does
      **not** change that box's password or address.
- [ ] *Restart* and *Shut down* do what they say, and the page recovers by itself after a restart.
- [ ] Add the page to the home screen: it opens like an app with the Sinko icon.

## C. Updates (needs two published releases, e.g. 3.0.0 and a test 3.0.1)

- [ ] On 3.0.0, *My box* shows "3.0.1 is available" within a day, or at once after *Check again*.
- [ ] *Update now*: progress shows, the page reloads at the end, `sinko --version` says 3.0.1, rules and devices are intact,
      filtering was down for no more than a few seconds (watch a ping/DNS lookup from a phone during the update).
- [ ] Unplug the internet and try again: a clear failure, nothing broken.
- [ ] Publish a deliberately broken 3.0.2 (installer exits 1, or fails the self-check): the box ends on 3.0.1, the page says
      it failed and why, and `sudo sinko doctor` is clean. Delete that release afterwards.
- [ ] `sudo sinko rollback` puts 3.0.0 back, offline.
- [ ] Automatic updates: switch on, set the box clock to 03:10 (`timedatectl set-time` with NTP off), wait; it updates once.
- [ ] Check what the box sent to GitHub: only requests for the release API and the three assets (watch `ss`/`tcpdump` or
      the proxy log).

## D. Upgrading an old box

- [ ] On a box running pihole-bahrain 2.2.0 (devices, bedtime, a timer running): `sudo pihole-bahrain update`. It ends on
      Sinko with devices, rules, bedtime, hostname and password intact; the old services and folders are gone;
      `pihole-bahrain doctor` still works (compatibility command); the lists in Pi-hole's admin point to the new address.

## E. The ready-made image

- [ ] Build a golden unit, seal it, image it, flash a **second** card, start it ([`product-image.md`](product-image.md)): the
      welcome screen appears, SSH host keys differ from the golden unit's, the machine id differs, `ssh root@` with the
      default password is refused, no leftover devices, history or logs, the box's address and `family.lan` are right even
      though the network differs from where it was built.
- [ ] Flash the same image on a **larger** card: the filesystem fills it.
- [ ] Pull the power five times during normal use: no corruption; the box starts every time.
- [ ] Leave it running for 72 hours with a few phones: no memory growth (`free -m`), temperature acceptable in the case,
      SD card writes modest (`cat /sys/block/mmcblk0/stat`, compare before and after).
- [ ] The hardware watchdog is armed (`systemctl show -p RuntimeWatchdogUSec`); optional stress test:
      `echo c | sudo tee /proc/sysrq-trigger` (only on a spare box) reboots it by itself.

## F. The counter (only when you deployed it)

- [ ] On a box, answer *Yes* in the page: within a few minutes the service's `/v1/stats` shows `online` going up by one.
      Answer *No*: nothing more is sent (`sinko telemetry payload` shows what would be; the service sees no new pings).
- [ ] The box's IP address and the family's names appear nowhere in the service's data.
