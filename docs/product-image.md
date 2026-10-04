# Making ready-made Sinko boxes (Orange Pi Zero 3)

The method is a **golden unit**: set up one box properly, *seal* it (remove everything that must be unique or private),
copy its card to an image file, and flash that image onto every card you ship. The first time a customer's box starts, it
personalises itself and shows a welcome screen where the parent chooses their own password.

You need: an Orange Pi Zero 3 (1 GB, 2 GB or 4 GB; **not 1.5 GB**), a good microSD card, Ethernet to a router with internet,
a computer to flash and copy cards. Read [`selling.md`](selling.md) first for the name, licence and claims rules.

## 1. Build the golden unit

1. Download **Armbian minimal, Debian Trixie** for the Orange Pi Zero 3 from <https://www.armbian.com/orange-pi-zero-3/>
   and flash it to a **small card (8 GB)**: the image you make is as big as this card, and every card you ship must be
   at least that big. The filesystem grows to fill larger cards on first start.
2. Start it with Ethernet, log in over SSH (`root` / `1234`), and finish Armbian's first-login questions (choose a strong
   throw-away root password; do not create a user; set the time zone and locale you ship with).
3. Install Sinko from the latest release: `curl -fsSL https://github.com/iret33/sinko/releases/latest/download/install.sh | sudo bash`
   (answer the counter question *no*: it is asked again in the customer's page). Open the page, check that it works.
4. Run `sudo sinko doctor` and `sudo sinko selfcheck`; fix anything that is not `ok`.

## 2. Seal it

```bash
sudo /opt/sinko/tools/seal.sh --dry-run     # shows everything it will remove, changes nothing
sudo /opt/sinko/tools/seal.sh               # asks you to type SEAL
```

Sealing removes: the parent password (so the page shows its welcome screen), children's devices, timers and bedtime, the
query history, logs and shell history, SSH host keys (each box makes its own on first start), the machine id, the counter's
install id and answer, cached updates, and Armbian's first-login wizard file. It locks the root password and turns off password
logins over SSH (`--keep-ssh-access` leaves them alone, for a test unit), sets the host name (`--hostname`, default `sinko`,
so `http://sinko.local` works), installs the hardware watchdog so a hung box reboots by itself, and arms the first-start service.
`--zerofill` writes zeros over the free space first, which makes the compressed image much smaller.

**Power the unit off now (`sudo poweroff`) and do not start it again before you image it.** Starting it runs the
first-start service, which uses up the seal.

> Support access: do **not** add your own SSH key to the image. It would be a back door into every customer's home network.
> If you need remote support, ask the customer to enable it for the session, or re-flash.

## 3. Image the card

On a Linux computer, with the card in a reader (replace `/dev/sdX`; check with `lsblk`):

```bash
sudo dd if=/dev/sdX of=sinko-3.0.0-orangepizero3.img bs=4M status=progress conv=fsync
xz -T0 -9 sinko-3.0.0-orangepizero3.img                       # about 0.5 to 1 GB with --zerofill
sha256sum sinko-3.0.0-orangepizero3.img.xz > sinko-3.0.0-orangepizero3.img.xz.sha256
```

Keep the image, its checksum and the Sinko version it contains together. Rebuild the golden unit for each new release
(boxes already in homes update themselves from the page; new cards should start with a recent version).

## 4. Flash and check each card

Flash with [balenaEtcher](https://etcher.balena.io) (it verifies what it wrote) or `xzcat image.img.xz | sudo dd of=/dev/sdX bs=4M conv=fsync`.
For every unit: card in the right size class, power supply tested, case closed, a quick-start card in the box
([`quick-start-card.md`](quick-start-card.md)). Every tenth unit (and the first of each batch), **start it once** on a real network and check:

- [ ] the page opens at `http://sinko.local` within 5 minutes and shows the **welcome screen** (no password set, no old devices);
- [ ] after choosing a password, the checklist appears and *My box* shows the current version and "up to date";
- [ ] `ssh root@<box>` with `1234` is refused;
- [ ] the box's address in the router list is its own (not the golden unit's) and the local name `family.lan` points at it.

A unit that has been started this way has used its first start; re-flash it before shipping.

## What a customer does

Plug in Ethernet and power, open `http://sinko.local`, choose a password, point the router's DNS at the box, add the
children's devices. The printed card says exactly that.

## If a customer forgets the password

The operating system login is locked, so there is no password to recover: re-flash the card (settings are lost; a backup from
*My box → Backup* restores the devices and rules). Tell customers to keep a backup somewhere safe.
