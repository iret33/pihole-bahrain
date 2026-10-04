# Troubleshooting

## An app still works after it was blocked

Go through these in order:

1. `sudo sinko doctor`. Every line should say `ok` or `info`. A `FIX` line says what is wrong and what to do; a `WARN`
   line is advice (for example that a child's device has sent no DNS query for a day, or that the network uses IPv6).
2. `sudo sinko diagnose`. A read-only report that ends with the likely causes, most likely first. It tests YouTube and
   Instagram; name other apps with `diagnose tiktok roblox`.
3. `sudo sinko watch`. Turn Wi-Fi off and on on the child's device, then open the app, or `m.youtube.com` in its
   **browser** (apps remember answers for a while). If no line appears, the device does not use the box for DNS. If it says
   "answered normally", no child rule matches that address.

The usual causes:

- **The device was not added** on the page. Rules apply only to added devices.
- **DNS never reaches the box.** The router relays DNS for everyone (then every query looks like it comes from the
  router), the router hands out its own IPv6 DNS server, or the phone uses Private DNS, a VPN, iCloud Private Relay or
  mobile data.
- **Another client row overrides the child's** (Pi-hole picks a device's groups from an IP or subnet row first, then its MAC
  address, then its host name). `doctor` and the page warn about this; remove the row, or give a row that covers only that
  one device the same groups.
- **The child was added by IP address** and the address changed. Run `sudo sinko use-mac` to register it by MAC address.
- **The device uses a random ("private") Wi-Fi address.** Turn that off for your home network on the child's device so
  it keeps the same address.

## I cannot open the page

- Try the address printed at the end of the install (`http://<box-ip>/`), then `http://<hostname>.local/` (works on
  iPhone, Mac and Windows; some Android phones need the number address), then `http://family.lan/` (works only once the
  router points at the box).
- Is the box on (green light), and is the Ethernet cable in the router?
- Look for the box in the router's list of connected devices; its name usually starts with the board name or `sinko`.
- On the box: `sudo sinko doctor`, `sudo systemctl status pihole-FTL sinko`.

## I forgot the parent password

- **Own install:** `sudo pihole setpassword`.
- **Ready-made box (no login to the operating system):** re-flash the card with the Sinko image. Settings are lost, and a
  box that is set up again shows the welcome screen. Keeping a **backup** (*My box → Backup*) makes this a five-minute job:
  after the new password, choose *Restore* and the devices and rules come back.

## The update did not finish

*My box* shows the reason. The box rolls back to the previous version by itself, so it keeps working. Run
`sudo sinko selfcheck` to see what is wrong, `sudo journalctl -u sinko -u sinko-update --since "1 hour ago"` for the log,
and `/var/log/sinko-install.log` for the installer's output. `sudo sinko update --force` tries again.
To go back by hand: `sudo sinko rollback`.

## The box has the wrong time

Bedtime and timers follow the box's clock. Boards such as the Orange Pi Zero 3 have no clock battery; they take the time
from the network a minute after start. `sudo sinko doctor` warns if the clock is not set. Check `timedatectl`; set the
zone with `sudo timedatectl set-timezone Asia/Bahrain` (or yours).

## Searching the lists by hand

Pi-hole stores these Adblock-style entries as `||youtube.com^`, not `youtube.com`, so searching for the bare domain finds
nothing even though it is blocked. Search for the stored form:

```bash
sudo pihole-FTL sqlite3 -readonly /etc/pihole/gravity.db "SELECT adlist_id, domain FROM gravity WHERE domain = '||youtube.com^'"
```

## Still stuck

Open an issue with the bug template and paste the output of `sudo sinko doctor` (it contains no passwords):
<https://github.com/iret33/sinko/issues/new/choose>
