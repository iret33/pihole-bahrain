# Selling ready-made Sinko boxes

Sinko is free software. Anyone may build their own box, and anyone may sell help or hardware. This page is for people who
sell **flashed devices or cards**, including the project maintainer's own shop. It is practical guidance, **not legal
advice**: check the points that apply where you sell.

## 1. The name and the logo

The code is GPL, but "Sinko" and the logo are marks of the project: read [`TRADEMARK.md`](../TRADEMARK.md). Short version:
flashed units sold as "Sinko" need the maintainer's permission; your own flashed units must carry your own name and say
"based on Sinko". Never put "Pi-hole" in a product name.

## 2. The software you are shipping, and what you owe its authors

A flashed card contains Debian (through Armbian), Pi-hole, Sinko and many other free-software packages. Handing it over
is distributing them, so for each unit:

* **Keep the licence texts on the card** (they are: `/opt/sinko/LICENSE`, `/opt/sinko/NOTICE`, `/usr/share/common-licenses`,
  Debian's per-package copyright files). Do not strip them when you shrink the image.
* **Offer the source.** For Sinko it is <https://github.com/iret33/sinko> at the tag that is on the card. For Pi-hole,
  Debian and Armbian, it is enough to give written, durable pointers to their source repositories **and** to offer to
  supply it on request for at least three years. Put this sentence on the leaflet or the product page:
  *"This product contains free software, including Sinko (GPL-3.0-or-later), Pi-hole (EUPL-1.2), Armbian and Debian.
  Licences and source code: <your page>, or write to <your contact> and we will send it."*
* Do not add terms that restrict what the buyer may do with that software (for example "no copying the card").
  A warranty that ends if they change it is fine; forbidding them to change it is not.
* If you modify Sinko, your modified source must be available under the GPL as well.

## 3. What you may claim

Sinko is a **DNS filter**. Say what it does and what it cannot do ("What it cannot do" in the README): a device on mobile
data or a VPN, apps that use fixed addresses, and a child who knows the Wi-Fi password and can change network settings can
all go around it. Do not write "child-proof", "100% safe", "unbypassable", "protects your children online", or anything
that promises more than the README does. Recommend Screen Time (iPhone) or Family Link (Android) next to it.

## 4. Hardware and the shop

* Use the Orange Pi Zero 3 with **1 GB, 2 GB or 4 GB** of RAM. **Not the 1.5 GB variant** (current kernels crash on it).
  2 GB is a comfortable default.
* Sell a **complete kit**: the board, a certified 5 V power supply with the right plug for your market, a high-endurance
  microSD card (16 GB is plenty; the filesystem grows to fill the card), a case with a little cooling, an Ethernet cable.
  A bad power supply or a worn card causes most "it stopped working" calls.
* Electrical safety, radio (the board has Wi-Fi and Bluetooth, which Sinko does not use; the box works on Ethernet),
  import and consumer-warranty rules differ by country (for example CE/UKCA in Europe, GCC conformity marking and TRA
  type approval for radio equipment in Bahrain and the Gulf). The power supply and the board vendor's declarations
  cover part of this; the seller of the finished kit is responsible for the rest.
* Decide and publish your warranty and returns policy, and how a customer gets help (a person who reads
  [`docs/troubleshooting.md`](troubleshooting.md) first, then you).

## 5. Privacy promise to buyers

The box keeps everything about the family on the box. It sends nothing about children, devices, websites or rules
anywhere. The only optional message is the anonymous "I am online" counter, **off until the parent says yes**
([`docs/privacy.md`](privacy.md)). Say this on the product page; it is a selling point, and it must stay true. If you
fork Sinko and add anything that sends data, tell buyers.

## 6. Making the units

Follow [`docs/product-image.md`](product-image.md) (golden unit, sealing, imaging, flashing, quality checks) and use
[`docs/quick-start-card.md`](quick-start-card.md) as the printed card in the box.
