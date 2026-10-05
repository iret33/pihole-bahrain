# Making ready-made Sinko boxes (Orange Pi Zero 3)

The method is a **golden unit**: set up one box properly, *seal* it (remove everything that must be unique or private),
copy its card to an image file, and flash that image onto every card you ship. The first time a customer's box starts, it
personalises itself and shows a welcome screen where the parent chooses their own password.

**A ready-made box has no login**, for the customer and for you: root is locked, SSH passwords are off, and there is no key
and no support account in the image. Everything in the customer-facing text, and everything you promise in your shop,
has to work with the page, the router, the power cable, or re-flashing. See [`selling.md`](selling.md), section 4, for how
to plan support around that.

You need: an Orange Pi Zero 3 (1 GB, 2 GB or 4 GB; **not 1.5 GB**), a good microSD card, Ethernet to a router with internet,
a computer to flash and copy cards. Read [`selling.md`](selling.md) first for the name, licence and claims rules.

## 1. Build the golden unit

1. Download **Armbian minimal, Debian Trixie** for the Orange Pi Zero 3 from <https://www.armbian.com/orange-pi-zero-3/>
   and flash it to a **small card (8 GB)**: the image you make is as big as this card, and every card you ship must be
   at least that big. About the size of the filesystem, see "How big the card is" below.
2. Start it with Ethernet (**DHCP, not a fixed address**: the first-start service looks for the address the box gets in
   the customer's home), log in over SSH (`root` / `1234`) and finish Armbian's first-login questions:
   * choose a strong throw-away root password (it is locked later);
   * answer **no** when it offers to connect to Wi-Fi: the box works on Ethernet, and a Wi-Fi password typed here would
     be kept on the card;
   * **do not create a user** (at "Creating a new user account" press Ctrl-C). Ctrl-C ends the wizard, so then set the time
     zone and locale you ship with by hand: `timedatectl set-timezone Asia/Bahrain` (or yours) and
     `dpkg-reconfigure locales`. If you made a user anyway, delete it before you seal (`userdel -r NAME`): it would keep its
     password and its `sudo` right on the console of every box.
3. Install Sinko from the latest release: `curl -fsSL https://github.com/iret33/sinko/releases/latest/download/install.sh | sudo bash`
   (answer the counter question *no*: it is asked again in the customer's page). Open the page, check that it works.
4. Run `sudo sinko doctor` and `sudo sinko selfcheck`; fix anything that is not `ok`.
5. Check what the customer's first minutes depend on, **before** you seal, because nothing can be repaired afterwards:
   * `systemctl is-enabled avahi-daemon sinko pihole-FTL sinko-lists.timer` all say `enabled`, and `avahi-daemon` is
     installed (it is what makes `http://sinko.local` work; if the install could not fetch it, install it now);
   * the box's address comes from DHCP (`ip -4 addr` shows `dynamic`), not from a fixed setting you made;
   * the console shows a login prompt, not a shell that is already logged in (Armbian removes its automatic root login
     when the first-login wizard has run; if you never ran the wizard on this unit, run it or remove the drop-ins under
     `/etc/systemd/system/getty@.service.d` and `serial-getty@.service.d` by hand).

## 2. Seal it

```bash
sudo /opt/sinko/tools/seal.sh --dry-run     # shows everything it will remove, changes nothing
sudo /opt/sinko/tools/seal.sh               # asks you to type SEAL
```

Sealing removes: the parent password, any application password and two-factor secret (so the page shows its welcome screen
and the parent is not locked out by yours), children's devices, timers and bedtime, Pi-hole's query history, logs
(including Armbian's on-disk copy in `/var/log.hdd`), shell history, SSH host keys (each box makes its own on first
start), Pi-hole's HTTPS key (each box makes its own), Wi-Fi profiles, the machine id, the counter's install id and answer,
and Armbian's first-login wizard file. It sets the host name
(`--hostname`, default `sinko`, so `http://sinko.local` works), installs the hardware watchdog so a hung box reboots by
itself, arms the first-start service, and **locks the root password and turns off SSH password logins last** (so a seal that
stops halfway leaves you able to log in and run it again; `--keep-ssh-access` leaves the lock off, for a test unit).
**It keeps** the Sinko program and `/var/lib/sinko/cache`, the copy of the installed release that lets a box go back to its
previous version without internet: that is on purpose, and it is not private.
By default it ends by writing zeros over the free space, which makes the compressed image much smaller (it takes a few
minutes; `--no-zerofill` skips it, for a test unit you will not image).

**Close every other SSH or console session, then run `unset HISTFILE; sudo poweroff` and do not start the unit again before
you image it.** Starting it runs the first-start service, which uses up the seal, and powering off from a login shell
writes that shell's history back (`unset HISTFILE` prevents it). Do not leave an editor or `less` open either.

> Support access: do **not** add your own SSH key or an account to the image. It would be a back door into every
> customer's home network. There is no remote support on a ready-made box (see [`selling.md`](selling.md), section 4).

## 3. Image the card

On a Linux computer, with the card in a reader (replace `/dev/sdX`; check with `lsblk`):

```bash
sudo dd if=/dev/sdX of=sinko-3.0.0-orangepizero3.img bs=4M status=progress conv=fsync
xz -T0 -9 sinko-3.0.0-orangepizero3.img                       # about 0.5 to 1 GB after zero-fill
sha256sum sinko-3.0.0-orangepizero3.img.xz > sinko-3.0.0-orangepizero3.img.xz.sha256
```

Keep the image, its checksum and the Sinko version it contains together. **Rebuild the golden unit for every Sinko release,
and whenever Pi-hole publishes a security fix**: boxes already in homes update Sinko themselves from the page, and
Debian's security updates reach them too, but **Pi-hole on a ready-made box is not updated by anything except a newer
image** (nobody can log in to run `pihole -up`, and Sinko's updater replaces only Sinko). New cards must start with a
recent image, and you offer your customers a re-flash (see [`selling.md`](selling.md), section 7). *My box*, *About* shows
which Pi-hole version a box runs.

If you want to be certain nothing of the golden unit's last minutes is on the image (the shutdown writes to the logs after
the seal has emptied them), scrub it on the computer before compressing; this is the one step that is yours to adapt, because the
partition number depends on the Armbian image:

```bash
sudo losetup -fP --show sinko-3.0.0-orangepizero3.img          # prints /dev/loopN; the root filesystem is /dev/loopNp1 on Armbian's usual layout
sudo mount /dev/loopNp1 /mnt
sudo rm -f /mnt/root/.bash_history /mnt/var/lib/systemd/random-seed
sudo rm -rf /mnt/var/log/journal/* /mnt/var/log.hdd/journal*
sudo find /mnt/var/log /mnt/var/log.hdd -type f -exec truncate -s0 {} +
sudo umount /mnt && sudo losetup -d /dev/loopN
```

### How big the card is

Armbian grows the root filesystem to fill the card **once, the first time a card starts**, and then switches that service off.
Your golden unit has already done that, so the image you copy already has the golden card's size, and the service is not
switched on again for a card flashed from it (unless the seal's plan says it re-arms it). What that means:

* a card **as big as the golden card or bigger** works: the extra space stays unused unless you grow the partition
  yourself after flashing (on a computer, with GParted, or `growpart` and `resize2fs`);
* a card smaller than the image does not work, and a card a little smaller than the golden card's nominal size may be
  refused by the flashing tool: use the same size class for every card you ship;
* Sinko and Pi-hole need only a couple of gigabytes, so the unused space does not matter for the product, only for what you
  promise on the product page: say "microSD card, 16 GB", not "uses the whole card". Hardware checklist E records what
  actually happens on a larger card (`lsblk`, `df -h /`).

## 4. Flash and check each card

Flash with [balenaEtcher](https://etcher.balena.io) (it verifies what it wrote) or `xzcat image.img.xz | sudo dd of=/dev/sdX bs=4M conv=fsync`.
For every unit: card in the right size class, power supply tested, case closed, a quick-start card in the box
([`quick-start-card.md`](quick-start-card.md)). Every tenth unit (and the first of each batch), **start it once** on a real network and check:

- [ ] the page opens at `http://sinko.local` within 5 minutes and shows the **welcome screen** (no password set, no old devices);
- [ ] after choosing a password, the checklist appears and *My box* shows the current version, the Pi-hole version and
      "up to date";
- [ ] `ssh -v root@<box>` (use the throw-away root password you set on the golden unit: `1234` would be refused on any card
      and proves nothing) is refused, and the methods it offers do **not** include `password` (only `publickey`, or none at all);
- [ ] the box's SSH host key differs from the golden unit's (`ssh-keyscan <box> | ssh-keygen -lf -` against the golden
      unit's fingerprint), and the machine id differs (`/etc/machine-id` on the tested card, mounted on a computer, against the golden unit's);
- [ ] the box's address in the router list is its own (not the golden unit's) and the local name `family.lan` points at it.

A unit that has been started this way has used its first start; re-flash it before shipping. A test unit that stays powered
on in a shared network has no password until someone claims it: re-flash it, and do not leave it running on a network
other people use.

## What a customer does

Plug in Ethernet and power, open `http://sinko.local`, choose a password **straight away**, point the router's DNS at the
box, add the children's devices. The printed card says exactly that. Until the parent chooses a password the box is open to
every device on the home network: that is an accepted limit of a box without a screen (see [`SECURITY.md`](../SECURITY.md)),
and the card says what to do if the page asks for a password the first time it is opened (someone else chose it: re-flash).

## If a customer forgets the password

The operating system login is locked, so there is no password to recover: re-flash the card (settings are lost; a backup from
*My box → Backup* restores the devices and rules). Tell customers to keep a backup somewhere safe.
