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
- [ ] The password route, on the real Pi-hole: on a new Pi-hole the installer sets the password through Pi-hole's API (type
      it at the prompt, and from a second shell `ps -eo args` shows it in no line while the installer runs). Run the
      installer again with `SINKO_PASSWORD=…` on that box, which now has a password: Pi-hole's API refuses (403 for the
      command-line session), the installer says it uses `pihole setpassword`, and the password works on the page and on
      `/admin/`. In that second case the password was briefly in `pihole`'s arguments, as the docs say (and in sudo's own
      line, because the test passes it as `SINKO_PASSWORD=…`, as the docs also say).
- [ ] The counter question follows the counter address. In a tree whose `TELEMETRY_URL` in `bin/sinko` is empty the
      interactive installer says nothing about the counter and the page shows no *Count this box* card and no checklist
      question; once the address is set (releasing step 5) the installer asks, and the page shows both.
- [ ] `http://<hostname>.local/` opens on an iPhone, a Mac or Windows PC, and on an Android phone (note which Android
      versions need the numeric address; the docs say "some").
- [ ] Router DNS pointed at the box: the page's checklist ticks "router" once a phone has used the box.
- [ ] Add a child's phone. Block YouTube: it stops in the app and in the browser. Allow it: it works again.
- [ ] Bedtime: set it two minutes ahead; internet goes off for the child's phone only, and returns at the end time.
- [ ] Reboot the box (power cut): everything comes back by itself, and the time is right within two minutes of the network.
      Bedtime and timers do not act on a clock that has not been set (they wait for the network time, for at most about ten minutes).
- [ ] Block list refresh: `sudo systemctl start sinko-lists` completes; rules still apply. Start it while `sudo sinko setup`
      runs: the second one waits for the first (both take `/var/lib/sinko/gravity.lock`) and nothing ends up with lists
      missing or doubled (`sudo sinko doctor`). Pi-hole's own weekly run (its cron, Sundays; look at `/etc/cron.d/pihole`
      for the time) and the page's restore do not take that lock: write down whether the nightly refresh (03:30 plus up to
      two hours) can land on the weekly one on this Pi-hole version.
- [ ] **The address follows the router.** Give the box another address (change its DHCP reservation, or move the unit to
      another network): within about five minutes the scheduler's address watch notices, `SINKO_IP` in `/etc/sinko/config`
      and the local name `family.lan` follow, `/pb/box.json` carries the new number (the page's router help shows that number,
      never a name), and `sudo sinko doctor` is clean. The router's own DNS setting is still the parent's to change.
- [ ] A system that speaks another language: on a unit whose locale is Arabic or German (`dpkg-reconfigure locales`, log in
      again), run the installer on a clean card: `avahi-daemon`, `unattended-upgrades` and a time service are installed all
      the same (the installer asks apt for its answers in English; `apt-cache policy` prints another word in another
      language), and `sinko.local` answers.
- [ ] Security updates and the clock are really set up: `apt-config dump | grep -i Unattended` shows the periodic setting
      "1" and no automatic restart, `systemctl list-timers 'apt-daily*'` lists both timers, and `timedatectl show -p
      NTPService -p NTPSynchronized` says the time service runs and the clock is synchronised a minute after the network
      is up. With `SINKO_OS_UPDATES=0` the installer sets up none of it, and an `unattended-upgrades` that was installed
      before Sinko is left as it was.
- [ ] **The clock after a power cut.** Cut the power twenty minutes after a bedtime ended, so that the clock the box
      starts with (the last time it saved, up to an hour old) is still inside the bedtime: while the box starts, the
      children's internet is never switched off again (ping from a child's phone every second); bedtime and timers wait
      for the clock for at most about ten minutes of uptime. Start another box with the network cable out for fifteen
      minutes and plug it in: when the time service sets the clock (a jump of hours), the page does **not** say the timer is
      not running, and the `at` in `/pb/box.json` follows the new clock within a minute. A slow `timedatectl` in the first
      minutes after the start (systemd's time service is started on demand, on a busy card) counts as "not
      synchronised yet": look at `sudo journalctl -u sinko -b` for the first passes.
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
      [test unit]). The Pi-hole version shown under *About* (read from Pi-hole each time the sheet opens) matches the *Core*
      line of `pihole -v` on a [test unit]; on a Pi-hole that cannot say (a development build) the row is simply left out and
      nothing else on the sheet changes.
- [ ] Scroll *My box* to its very end by touch on both phones: the sheet does not slide away or leave a blank page.
- [ ] *Change password*: the new password works, the old one does not, the scheduler keeps running (`systemctl status sinko`).
- [ ] *Backup* downloads a file; *Restore* of that file on a second box brings back devices, rules, timers, bedtime, and does
      **not** change that box's password or address. Restore a backup made **before** a migration or from a box that had
      other lists: the page says honestly what came back, and `sudo sinko doctor` is clean afterwards.
- [ ] *Restart* and *Shut down* do what they say, and the page recovers by itself after a restart. After *Shut down* the box
      **stays off** for ten minutes. The seal's watchdog setting is meant to leave a power-off alone (in the source of
      systemd 257, the one in Debian Trixie, the watchdog is disarmed before a power-off), but only the board can show
      it: a kernel built
      with `CONFIG_WATCHDOG_NOWAYOUT`, or a power-off that does not cut the power, would bring the box back by itself. If it
      comes back, that is a bug: write down the board, the kernel (`uname -r`) and `journalctl -b -1 | tail -n 30`.
- [ ] **The restore window.** Take a backup on box A while a *Restart* request is waiting [test unit: `sudo systemctl stop
      sinko`, press *Restart* on the page, take the backup, `sudo systemctl start sinko`], wait twenty minutes, then restore
      that file on box B (a request that box B never handled looks new to it). B does not restart, update or switch off
      because of what the file carried (`sudo journalctl -u sinko` says the request was ignored as too old), and the restore
      ends with every list registered (`sudo sinko doctor`). Repeat it five times: the restore takes seconds on the real
      Pi-hole and the scheduler passes every 15 seconds, so a pass can fall inside it (the automated test makes one fall
      there against the stand-in only). Write down how long the restore and the rebuild of the lists take.
- [ ] Unplug the box with the page open: within a minute or so the page says it cannot reach the box, and does not go on
      showing "Filtering: On" or "Last device seen: just now". Plug it in: the page recovers by itself.
- [ ] Add the page to the home screen: it opens like an app with the Sinko icon.
- [ ] *My box → Privacy* opens the privacy statement (`docs/privacy.md`), not the top of the README.

## C. Updates (needs two published releases, e.g. 3.0.0 and a test 3.0.1)

- [ ] On 3.0.0, *My box* shows "3.0.1 is available" within a day, or at once after *Check again*. The first check comes
      about two minutes after the box starts (`sudo sinko status`: `update.checked` moves; or the proxy log shows a request
      to `api.github.com`), then about once a day. With the internet unplugged the page shows nothing alarming and the box
      asks again after half an hour to an hour (not tomorrow). A box pinned with `sudo sinko update --ref v3.0.1` makes
      no request to `api.github.com` at all.
- [ ] *Update now*: progress shows, the page reloads at the end, `sinko --version` says 3.0.1, rules and devices are intact,
      filtering was down for no more than about a minute (watch a ping/DNS lookup from a phone during the update).
- [ ] *Restart* or *Shut down* pressed during an update: the box ignores it, the update finishes, and the box does not
      restart or power off later because of it.
- [ ] After the update, a phone that had the page open shows the new version's page and not a mixture: Pi-hole's real web
      server lets a browser keep a file for an hour, so the page's scripts carry `?v=<version>` in their addresses. Check
      on the real Pi-hole that `curl -sI 'http://<box>/pb/app.js?v=3.0.0'` answers `200` (the part after the `?` is ignored
      by its static file handler) and look at its `Cache-Control` header. Then open the page on a second phone that has not
      been told anything: it says its own version in the footer and reloads once into the new one.
- [ ] Unplug the internet and try again: a clear failure, nothing broken.
- [ ] Not enough room: on a [test unit] leave less than 200 MB free (`fallocate -l …G /var/tmp/fill`): *Update now* fails
      at once, the page says the previous version is back (a check of the untouched box passed), `sudo journalctl -u
      sinko-update` says how much is free and that nothing was changed, and a second try after deleting the file works.
      A first installation on a card with less than 1 GB free refuses in its first lines and changes nothing (exit status 75).
- [ ] **The self-check on the slowest board.** On a 1 GB Orange Pi Zero 3 with the slowest card you sell, and again while a
      phone is using the box and a block-list refresh runs, a real update to a newer test release passes its self-check.
      The scheduler has to be up for at least 20 seconds and to have finished two passes of the new version, and the update
      waits at most 90 seconds for that after the installer restarts it. Write down how long it took from the restart to
      "ok" (`sudo journalctl -u sinko-update`, `sudo journalctl -u sinko`); if it comes near 90 seconds, the limit is too
      tight and a good update will be rolled back on slow cards.
- [ ] **systemd-run and the reboot.** While an update runs, `systemctl status sinko-update` shows the transient unit
      (`systemd-run --unit sinko-update --collect`), the runner keeps going while the installer restarts `sinko.service`,
      and nothing is left failed afterwards (`systemctl --failed`). The page's *Restart* and *Shut down* run `systemctl
      reboot` and `systemctl poweroff` from the service: the box does exactly that, and after the start the same request
      does not fire again (`/var/lib/sinko/handled.json` holds its marker).
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
- [ ] **A cut between the program and the page.** [test unit] Start an update to a newer test release and watch
      `ls -l --time-style=full-iso /opt/sinko/bin/sinko`: the moment it changes, pull the power (or `sudo systemctl kill -s
      KILL sinko-update`, then `sudo reboot -f`). After the start the box still filters, and `sudo sinko selfcheck` says the
      page and the program are of different versions. With the network cable in, within about a quarter of an hour (the
      box first reports the cut update after about three minutes, then ten minutes of difference, a minute for the look,
      then the installer; with no time server the clock is believed only after ten minutes of uptime, so allow about ten
      minutes more) the box repairs itself: `sudo journalctl -u sinko` says the page and the program
      have been of different versions and that it installs this version again, `sudo journalctl -u sinko-repair` has the
      installer's output, `/var/lib/sinko/repair.json` exists, the selfcheck passes and *My box* says the update worked. Do it once more with the network cable out during the repair:
      write down whether the installer's `sinko setup` (it runs `pihole -g`) gets through with the lists the box already
      has, because the repair downloads nothing of Sinko itself but a refresh of the lists still asks the internet.
- [ ] **A cut after the page swap.** Same, but pull the power a few seconds later, while the services are set up
      (`systemctl status sinko` shows a start in the last seconds). Write down what *My box* says after three minutes and
      whether anything repairs the box without a person: the repair above does not cover this case (page and program
      already agree), so for a ready-made box the answer is the page's advice, then a re-flash.
- [ ] `sudo sinko rollback` puts 3.0.0 back, offline.
- [ ] `sudo sinko update --ref v3.0.1` pins the box: the page then offers nothing newer, and `sinko update --check` says it is
      pinned. `sudo sinko update --ref latest` (with `--force` when already on the newest) follows releases again.
- [ ] Automatic updates: switch on, set the box clock to 03:10 (`timedatectl set-time` with NTP off), wait; it updates once.
- [ ] **A failed night is tried again the same night.** Automatic updates on, a newer release waiting, the internet unplugged
      at 03:05 on the box clock: the attempt fails (the page says it did not work), and after the cable is back the box
      tries the same release again after about half an hour, inside 03:00 to 05:00, and it works. A release that fails for
      another reason (a test release that fails its self-check) is **not** tried again for a week.
- [ ] Check what the box sent to GitHub during an update: the release API, then `sinko.tar.gz` and `sinko.tar.gz.sha256`
      (`github.com`, passed on to GitHub's download host), then, from the installer the update runs, one request for each
      block list (`pihole -g`) and one for `lists/guard.txt` (the doctor check), all to `raw.githubusercontent.com`, and
      nothing else (watch `ss`/`tcpdump` or the proxy log). After the update the box asks the release API again about two
      minutes after the scheduler restarts. (`install.sh` is downloaded only by a new install.)

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
      </dev/null | openssl x509 -noout -fingerprint`) is different on two cards flashed from the same image. At the first
      start `sudo journalctl -u sinko-firstboot` [test unit] says either "Pi-hole has its own HTTPS key" (FTL wrote it
      within ten seconds) or "Pi-hole has not written an HTTPS key: restarting it so that it makes one" followed by
      "Pi-hole made its own HTTPS key" (one restart of FTL, up to thirty seconds more); on both paths the page opens
      within five minutes of the power, `/etc/pihole/tls.pem` can be read by FTL (it is written by FTL, not by the
      script), and the unit ends with "first start finished". Do it with a cable in and with none (the key does not wait
      for the network).
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
- [ ] The hardware watchdog is armed (`systemctl show -p RuntimeWatchdogUSec -p RebootWatchdogUSec` say 15 s each) and
      `journalctl -b | grep -i watchdog` shows no complaint about the timeout (the board's driver accepts 16 seconds at
      most; a longer time is refused and the watchdog would be off without a word); optional stress test: `echo c | sudo
      tee /proc/sysrq-trigger` (only on a spare box) reboots it by itself.
- [ ] **Shut down and the watchdog.** After the page's *Shut down* the board's power-off really cuts the power and the box
      stays off for ten minutes. (Checked in the source of systemd 257 only: PID 1 disarms the watchdog before a power-off.
      Whether the board's driver lets it be disarmed, and whether its power-off cuts the power, only the board shows.) If
      the box restarts by itself, write down the board, the kernel and the log, and take the watchdog setting out of the
      seal until it is understood.
- [ ] **Restart with a backlog on the card.** *Restart* right after a block-list refresh and right after an update, ten
      times, on the slowest card you sell: the box comes back each time, and `sudo journalctl -b -1` ends with an orderly
      shutdown. A reboot keeps the watchdog armed at 15 seconds, and systemd feeds it once per unmount pass, not during
      the first sync of the card: a sync that takes longer than 15 seconds resets the box in the middle of the shutdown
      (the file system recovers from its journal at the next start). Write down any reset, and the card.
- [ ] The seal stops on a unit that is not ready, on a real golden unit: with a second login on the console (`adduser
      test`), with `unattended-upgrades` removed or `APT::Periodic::Unattended-Upgrade "0"`, with no time service
      (`systemctl disable --now systemd-timesyncd`), and with the unit pinned (`sudo sinko update --ref v3.0.1`) or on a
      branch, each ends with its own message and changes nothing (`--dry-run` lists the same problems). With all of them put
      right it seals.
- [ ] **The SSH read-back.** The seal deletes the host keys before it locks SSH, so it asks `sshd -G` (or `sshd -T -h` with
      a throw-away key on an older sshd) what the lock-down says: on the real Debian Trixie `sshd` it reports password
      logins off. If it cannot report, the seal fails and takes its drop-in out again, so the unit can still be logged in
      to.
- [ ] **The zero-fill on a real card** ends because the disk is full ("No space left on device"), removes its file
      (`/.sinko-zerofill`), leaves `df` showing free space, and the compressed image is well below the card's size. Pull
      the power during it on a spare unit: the next seal and the first start both delete the leftover file.
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
- [ ] The Pages site is live and shows the screenshots that are committed in `docs/img/` (the "See it" section, no empty
      frames). The numbers strip shows real numbers; with the counter off, only downloads. Its "Full instructions on
      GitHub" link opens the README at its *Install* section, and its install command is the hardened one
      (`--proto '=https' --proto-redir '=https'`).
- [ ] **"Before you publish"** in [`docs/maintainers/releasing.md`](maintainers/releasing.md) was done in its order, and
      every link and badge in the README, in *My box → About* and on the website that it lists opens what it should.
- [ ] The old `iret33/pihole-bahrain` address still redirects (web, git, `raw.githubusercontent.com`) after the rename, and a
      2.x box updates through it.
- [ ] `TELEMETRY_URL` in `bin/sinko` and `statsUrl` in `site/config.js` are set to the deployed counter **before** the tag, and
      that counter has `/v1/forget`.
