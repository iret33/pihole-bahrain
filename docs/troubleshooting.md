# Troubleshooting

*Commands that start with `sudo` are for your own install, typed on the box over SSH. A **ready-made box has no login at
all**: for you, everything is on the page, the router, the power cable, or re-flashing the card. The section
[Ready-made box](#ready-made-box-no-login) below has the steps for it.*

## An app still works after it was blocked

Own install, in this order:

1. `sudo sinko doctor`. Every line should say `ok` or `info`. A `FIX` line says what is wrong and what to do; a `WARN`
   line is advice (for example that a child's device has sent no DNS query for a day, or that the network uses IPv6).
2. `sudo sinko diagnose`. A read-only report that ends with the likely causes, most likely first. It tests YouTube and
   Instagram; name other apps with `diagnose tiktok roblox`.
3. `sudo sinko watch`. Turn Wi-Fi off and on on the child's device, then open the app, or `m.youtube.com` in its
   **browser** (apps remember answers for a while). If no line appears, the device does not use the box for DNS. If it says
   "answered normally", no child rule matches that address.

Ready-made box: no commands, but the same checks by eye. In the page, is the device added, and does the live picture
show it asking when you open the app (turn Wi-Fi off and on on the child's device first)? In the router, is the DNS server
the box's address (and only that)? If the device never shows up, the cause is below, under "DNS never reaches the box".

The usual causes:

- **The device was not added** on the page. Rules apply only to added devices.
- **DNS never reaches the box.** The router relays DNS for everyone (then every query looks like it comes from the
  router), the router hands out its own IPv6 DNS server, or the phone uses Private DNS, a VPN, iCloud Private Relay or
  mobile data.
- **Another client row overrides the child's** (Pi-hole picks a device's groups from an IP or subnet row first, then its MAC
  address, then its host name). `doctor` and the page warn about this; remove the row (in Pi-hole's admin page, *Clients*:
  `/admin` on the box's address, with the parent password), or give a row that covers only that one device the same groups.
- **The child was added by IP address** and the address changed. On an own install, `sudo sinko use-mac` registers it by
  MAC address; on a ready-made box, remove the device on the page and add it again after it has used the box.
- **The device uses a random ("private") Wi-Fi address.** Turn that off for your home network on the child's device so
  it keeps the same address.

## I cannot open the page

- Try the address printed at the end of the install (`http://<box-ip>/`), then `http://<hostname>.local/` (works on
  iPhone, Mac and Windows; some Android phones need the number address), then `http://family.lan/` (works only once the
  router points at the box).
- Is the box on (green light), and is the Ethernet cable in the router?
- Look for the box in the router's list of connected devices; its name usually starts with the board name or `sinko`.
- Own install, on the box: `sudo sinko doctor`, `sudo systemctl status pihole-FTL sinko`.
- Ready-made box: after a power cut it needs about two minutes, and the very first start about five. Then see below.

## I forgot the parent password

- **Own install:** `sudo pihole setpassword`.
- **Ready-made box (no login to the operating system):** re-flash the card with the Sinko image. Settings are lost, and a
  box that is set up again shows the welcome screen. Keeping a **backup** (*My box → Backup*) makes this a five-minute job:
  after the new password, choose *Restore* and the devices and rules come back.

## The update did not finish

*My box* shows what happened, and it says one of three things (see [`updating.md`](updating.md), "How an update can end"):
it **worked**; it **failed and the previous version is back** (the box keeps working as before); or it **failed and the
previous version could not be put back**. Only in the second case is the old version running again. In the third, restart
the box (unplug it, wait ten seconds, plug it in, give it two minutes) and press *Try again* if the page offers it; if it
still misbehaves, restore from a backup or re-flash (ready-made box), or use the commands below (own install).

**The power failed in the middle of an update.** Leave the box on for about a quarter of an hour. A box that ended up with
its page and its program of two versions installs the version again by itself from the copy it keeps (nothing is
downloaded), and *My box* then says the update worked. If it still says it did not finish after an hour, and the page
offers *Update now* or *Try again*, press it; if not, take a backup (while the page opens) and re-flash the card. The
details are in [`updating.md`](updating.md), "If the power fails during an update".

Own install: `sudo sinko selfcheck` says what is wrong, `sudo journalctl -u sinko -u sinko-update --since "1 hour ago"` is
the log, and `/var/log/sinko-install.log` has the installer's output. `sudo sinko repair` installs the version that is
running again from the copy on the box (it does nothing when the page and the program already agree), and `sudo sinko
update --force` installs the newest release again. To go back by hand: `sudo sinko rollback`.

## The box has the wrong time

Bedtime and timers follow the box's clock. Boards such as the Orange Pi Zero 3 have no clock battery; they take the time
from the network a minute after start, and bedtime and timers wait until the clock is believed (for at most about ten
minutes). Own install: `sudo sinko doctor` warns if the clock is not set; check `timedatectl` (the installer sets up
`systemd-timesyncd` when the box has no time service at all, and leaves any other one as it is); set the zone with
`sudo timedatectl set-timezone Asia/Bahrain` (or yours). Ready-made box: the clock row in *My box* compares the box's clock
with your phone; if they differ, leave the box on with internet for a few minutes. The time **zone** is the one the seller
built the box with and a parent cannot change it: if bedtime starts at the wrong hour although the clock row agrees, set
the times on the page by the difference, or ask your seller for a box with your zone.

## Searching the lists by hand

Own install only. Pi-hole stores these Adblock-style entries as `||youtube.com^`, not `youtube.com`, so searching for the
bare domain finds nothing even though it is blocked. Search for the stored form:

```bash
sudo pihole-FTL sqlite3 -readonly /etc/pihole/gravity.db "SELECT adlist_id, domain FROM gravity WHERE domain = '||youtube.com^'"
```

## Ready-made box (no login)

A ready-made box has no login of any kind, on purpose: nobody can log in to it, not you, not the seller, and not a child who
guesses a password. So the commands above do not exist for you, and you do not need them. In order:

1. **The page.** *My box* shows whether filtering is on, the box's temperature and memory, the last device seen and the
   clock, and has *Update now*, *Backup*, *Restart* and *Shut down*. Many problems end with *Restart*.
2. **The power cable.** Unplug the box, wait ten seconds, plug it in, and give it two minutes (five on the very first
   start). A box that was switched off in the middle of an update checks itself when it starts again.
3. **The router.** In the router's list of connected devices the box should be there with its own address; the router's DNS
   server should be that address, and the address should be reserved for the box. An address that changed is repaired by the
   box itself within a few minutes, but the router's DNS setting must follow it.
4. **Re-flash the card.** The last resort, and a five-minute one if you keep a backup: take a *Backup* from *My box*
   (while the page still opens), write the Sinko image to the card again, start the box, choose a password on the welcome
   screen, then *Restore*. Re-flashing also brings a newer Pi-hole: Pi-hole on a ready-made box is updated only that way.
5. **Ask your seller.** They cannot connect to your box either. Send them a screenshot of *My box* and of the router's list
   of connected devices, what lights are on, and what you did.

If you open the page for the first time and it asks for a password instead of showing the welcome screen, someone else
on your network chose a password first. Re-flash the card, and do the welcome screen straight away next time.

## Still stuck

Open an issue with the bug template: <https://github.com/iret33/sinko/issues/new/choose>. **The issue is public.** On an
own install, paste the output of `sudo sinko doctor`: it has no passwords, but it can contain your children's names (as you
typed them), their devices' MAC or IP addresses and your router's address, so read it first and replace those (`Child 1`,
`aa:bb:cc:...`). On a ready-made box, describe what you see, or attach a screenshot of *My box* with any names hidden.
