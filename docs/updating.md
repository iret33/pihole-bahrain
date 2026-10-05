# Updating Sinko

*Commands that start with `sudo` are for your own install, typed on the box over SSH. A ready-made box has no login at
all: everything about Sinko's own updates is on the page, and for Pi-hole see "Pi-hole and the operating system" below.*

## From the page

Open **My box**. If a newer version exists it says so, with a link to what changed. Press **Update now**. The page shows
progress and reloads itself when it is done (it then runs the new version completely: its scripts and styles are asked for
by the release's number, because a phone keeps files from the box for an hour; a second phone that still shows the old page
reloads itself once, as soon as it notices that the box is on a newer version). The children's internet keeps working while
it updates, but it may pause for about a minute when the services restart. Leave the box plugged in until the page says it
is finished. If you press *Restart* or *Shut down* while an update is running, the box ignores the request: wait for the
update to end and ask again.

Switch on **Update automatically at night** to let the box do it between 03:00 and 05:00 (box time). The box looks for a
new version about two minutes after it starts and then about once a day (and again after half an hour to an hour when
GitHub could not be reached; [`privacy.md`](privacy.md) says what that asks of GitHub), and installs one it has found
inside that window. If a night's attempt fails only because the download could not be made (the internet) or the card
has too little room, the box tries the same version again later in the same night (after half an hour, while the window
lasts). A version that failed for any other reason (the installer or the self-check failed, for example) is left alone for
a week. Two attempts are never less than five minutes apart.

## How an update can end

After an update the box checks that it works. *My box* then says one of three things:

* **Updated.** The new version passed its check.
* **It failed and the previous version is back.** The box put the version you had before back by itself, and passed the
  same check. Nothing is lost and you can carry on; the reason is shown to whoever helps you.
* **It failed and the previous version could not be put back** (going back did not work, or there was nothing to go back
  to). This is the one to take seriously: the box may not work properly. The page does **not** say that the previous
  version is back, because it is not. Do what the page says: unplug the box, wait ten seconds, plug it in and give it two
  minutes. If the page then offers **Try again** (it does while a newer version is waiting), you can press it. If the
  rules or bedtime still do not work: on an own install, `sudo sinko selfcheck` says what is wrong and `sudo sinko
  rollback` puts the previous version back by hand; on a ready-made box, make sure you have a backup (*My box → Backup*)
  and re-flash the card (see "Pi-hole and the operating system").

Rarely the page says instead that Sinko could not check whether the box still works as before (an update that could not
even be started, for example). Nothing is claimed then: watch the rules and bedtime, and if they stop working, treat it
like the third ending.

A failure that happens before the installer has changed anything (the download, the checksum, too little room on the
card) leaves the box exactly as it was; the page then says the previous version is back only after the box has checked
itself and passed.

## If the power fails during an update

The page asks you to leave the box plugged in, because a box has no battery and can lose power at any moment. If it
happens anyway, this is what can be left, and what happens next:

* **No file is left half-written.** The installer puts each file in place under a temporary name and renames it, and it
  flushes the card before and after, so after a cut every file is either the old one or the new one, never an empty one.
* **The box looks at itself when it starts again.** When its clock is right, and about three minutes after the page said
  "updating" with nothing running the update, it checks the box and reports one of the three endings above, so a box
  that stopped halfway does not sit on "updating" for ever.
* **The page and the program can be of two versions.** If the cut falls in the few seconds between the installer
  replacing the program and replacing the page (or the other way round, after a rollback), the box still blocks sites and
  keeps bedtime and timers, but the page is of one version and the program of the other, and the update may be reported
  as not finished. The box repairs this by itself. When the two have been of different versions for ten minutes, with
  nothing installing and the box's clock right, it installs this version again from the copy it keeps of it (nothing of
  Sinko is downloaded), at most once an hour, and then *My box* says the update worked. After a power cut in the middle
  of an update, **leave the box on for about a quarter of an hour** before you do anything else.
* **If it is still wrong after an hour.** If the page offers **Update now** or **Try again**, press it. If it does not,
  on a ready-made box take a backup while the page still opens, and re-flash the card (see below): devices, apps and rules
  come back with *Restore*. On an own install, `sudo sinko repair` installs the version again from the copy on the box,
  `sudo sinko selfcheck` says what is wrong, and `sudo sinko update --force` installs the newest release again.
* **A cut late in the update**, after the page and the program already agree and while the services are being set up, is
  not repaired by the box as a whole. Groups or block lists that are missing are put back by the box itself (at most once
  an hour), and the check described above reports what it finds; for anything else the advice of the previous point
  applies.

## From the box (own install)

```bash
sudo sinko update --check     # is there a newer version? (changes nothing)
sudo sinko update             # update now
sudo sinko rollback           # go back to the version before the last update (works offline)
sudo sinko selfcheck          # quick health check of the installation
```

### Pinning a version, and following releases again

```bash
sudo sinko update --ref v3.0.1          # install exactly this release and stay on it (add --force to go back to an older one)
sudo sinko update --ref latest          # follow the newest release again (add --force if you already have the newest)
```

`--ref` **pins the box**. The version you name is saved as `SINKO_REF` in `/etc/sinko/config`, and from then on the page and
the night update follow that version only: the page will not offer a newer release, *Update automatically at night* does
nothing, and `sudo sinko update --check` says that the box is pinned and which version it is pinned to. Security fixes stop
reaching a pinned box. The command prints this when it finishes, and how to undo it: `sudo sinko update --ref latest`
(with `--force` when the box already has the newest release). Setting `SINKO_REF=latest` in `/etc/sinko/config` does the
same. The choice is saved only when the update has worked: if `sudo sinko update --ref v3.2.0` fails (and the box goes
back), the box keeps following what it followed before, and the command says which that is.

## What an update does, and how it protects you

1. Looks for the newest GitHub release of this project (not drafts or pre-releases).
2. Checks that the card has room: **more than 200 MB free** for an update or a rollback (a first installation needs more
   than 1 GB). If there is not enough, it stops before anything is downloaded or changed, and says how much is free.
3. Downloads `sinko.tar.gz` and its SHA-256 checksum over HTTPS and refuses to continue if they do not match, or if the
   archive contains anything that would be written outside its own folder, or a link. The update address must be an
   `https` one.
4. Keeps the archive of the version that was installed, and of the one before it, so that the previous version can be put
   back without internet.
5. Runs the new installer, which sets everything up again, refreshes the block lists (so the box asks GitHub more than
   once a day on an update day; [`privacy.md`](privacy.md) lists it all) and ends with the same check as `sudo sinko
   doctor`. Then a self-check: the page and the box program agree on the version, the services run, the rules are in
   place, and the box's scheduler is working.
6. **If the installer or the self-check fails, it tries to put the previous version back by itself**, and tells you which
   ending it was (see "How an update can end").

Your password, devices, rules, timers and bedtime are never touched by an update. Updates only ever come from the
repository named in `/etc/sinko/config`; the page can ask the box to update but cannot choose the source.

**What the checksum does and does not prove.** The checksum is downloaded from the same place as the archive, so it protects
you against a damaged or half-finished download, not against someone who controls the project's GitHub repository or its
release files: they decide what every box installs, and the box has no second key to check. [`SECURITY.md`](../SECURITY.md)
says what protects the repository and what a seller can add.

## Pi-hole and the operating system

**Own install.** Sinko updates only itself. Pi-hole has its own updates (`sudo pihole -up`); do them when you are at home
and watch that the page still works. Debian security updates are installed automatically by default (`SINKO_OS_UPDATES=0`
at install time turns that off); the box never restarts by itself for them, so restart it from **My box** once in a while.

**Ready-made box.** Nobody can log in to it, and Sinko's updater replaces only Sinko, so **Pi-hole on a ready-made box is
updated by re-flashing a newer Sinko image**, and the seller rebuilds the image for each release (see
[`selling.md`](selling.md)). *My box*, *About* shows which Pi-hole version your box runs. Debian's security updates still
arrive by themselves, and Sinko still updates itself from the page. To re-flash: make a backup first (*My box → Backup*),
write the new image to the card (or use the card your seller sends), start the box, choose a password on the welcome screen
and use *Restore*: devices, apps and rules come back. Then point the router's DNS at the box again if its address changed.

## Working on a development version

`sudo sinko update --ref master` installs the branch from Git instead of a release, and keeps the box on it. On such a box
the page does not look for releases and never offers one, and the version number tells you which code you are running.
To go back to releases: `sudo sinko update --ref latest --force`.
