# Updating Sinko

## From the page

Open **My box**. If a newer version exists it says so, with a link to what changed. Press **Update now**. The page shows
progress and reloads itself when it is done. The children's internet keeps working while it updates; filtering pauses for
a few seconds when the services restart.

Switch on **Update automatically at night** to let the box do it between 03:00 and 05:00 (box time). It tries a version
once a day at most, and never retries a version that failed for a week.

## From the box

```bash
sudo sinko update --check     # is there a newer version? (changes nothing)
sudo sinko update             # update now
sudo sinko update --ref v3.0.1   # install exactly this release (also for going back to an older one)
sudo sinko rollback           # go back to the version before the last update (works offline)
sudo sinko selfcheck          # quick health check of the installation
```

## What an update does, and how it protects you

1. Looks for the newest GitHub release of this project (not drafts or pre-releases).
2. Downloads `sinko.tar.gz` and its SHA-256 checksum over HTTPS and refuses to continue if they do not match, or if the
   archive contains anything that would be written outside its own folder.
3. Keeps the downloaded archive (the last two) so the previous version can be put back without internet.
4. Runs the new installer, then a self-check (the page and the box program agree on the version, the services run, the
   rules are in place).
5. **If the installer or the self-check fails, it puts the previous version back by itself** and tells you why.

Your password, devices, rules, timers and bedtime are never touched by an update. Updates only ever come from the
repository named in `/etc/sinko/config`; the page can ask the box to update but cannot choose the source.

## Pi-hole and the operating system

Sinko updates only itself. Pi-hole has its own updates (`sudo pihole -up`); do them when you are at home and watch that
the page still works. Debian security updates are installed automatically by default (`SINKO_OS_UPDATES=0` at install time
turns that off); the box never reboots by itself for them, so restart it from **My box** once in a while.

## Working on a development version

`sudo sinko update --ref master` installs the branch from Git instead of a release. Release updates from the page
continue to offer the newest release; the version number tells you which one you are running.
