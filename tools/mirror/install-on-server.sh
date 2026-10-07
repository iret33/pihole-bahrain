#!/usr/bin/env bash
# Sets up updates.ioai.bh and sinko.ioai.bh on the ioai.bh server. Run as root ON that server; safe to run again.
#
#   scp tools/mirror/sinko-dist-sync.py tools/mirror/systemd/sinko-dist-sync.{service,timer} \
#       tools/mirror/nginx/{updates,sinko}.ioai.bh.conf root@149.104.106.5:/root/
#   ssh root@149.104.106.5 'bash -s' < tools/mirror/install-on-server.sh
#
# What it does, in this order (each step checks before it changes anything):
#   1. a system user sinko-dist, the folders it fills, the sync program and its systemd timer
#   2. Let's Encrypt certificates for both names (webroot /var/www/letsencrypt, as for ioai.bh and pm.ioai.bh),
#      through a temporary port-80-only nginx file while no certificate exists yet
#   3. the two nginx sites; nginx is reloaded only when `nginx -t` passes, otherwise the new files are taken out again
#   4. the timer is started and the first sync runs, then the addresses are checked
# Nothing of ioai.bh or pm.ioai.bh is touched. To undo: disable the timer, remove the two site files and reload nginx.
set -Eeuo pipefail

SRC="${SRC:-/root}"
NAMES=(updates.ioai.bh sinko.ioai.bh)
say() { printf '\n== %s\n' "$*"; }
die() { printf 'install-on-server.sh: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root"
for f in sinko-dist-sync.py sinko-dist-sync.service sinko-dist-sync.timer updates.ioai.bh.conf sinko.ioai.bh.conf; do
  [[ -f "$SRC/$f" ]] || die "$SRC/$f is missing (copy the five files first, see the top of this script)"
done
for name in "${NAMES[@]}"; do
  ip="$(getent ahostsv4 "$name" | awk 'NR==1{print $1}')"
  [[ -n "$ip" ]] || die "$name does not resolve yet: add its A record (149.104.106.5) at AEserver first"
done

say "1. user, folders, program, timer"
id sinko-dist >/dev/null 2>&1 || useradd --system --home-dir /var/lib/sinko-dist --shell /usr/sbin/nologin sinko-dist
install -d -m 0755 -o root -g root /opt/sinko-dist
install -m 0755 -o root -g root "$SRC/sinko-dist-sync.py" /opt/sinko-dist/sinko-dist-sync.py
install -d -m 0755 -o sinko-dist -g sinko-dist /var/www/updates.ioai.bh /var/www/sinko-site
install -d -m 0700 -o sinko-dist -g sinko-dist /var/lib/sinko-dist
install -d -m 0750 -o root -g sinko-dist /etc/sinko-dist
if [[ -f /etc/sinko-dist/github-token ]]; then chown root:sinko-dist /etc/sinko-dist/github-token; chmod 0640 /etc/sinko-dist/github-token; fi
install -m 0644 "$SRC/sinko-dist-sync.service" "$SRC/sinko-dist-sync.timer" /etc/systemd/system/
systemctl daemon-reload
echo "ok"

say "2. certificates"
boot=/etc/nginx/sites-available/sinko-dist-bootstrap.conf
need=()
for name in "${NAMES[@]}"; do [[ -d "/etc/letsencrypt/live/$name" ]] || need+=("$name"); done
if ((${#need[@]})); then
  {
    for name in "${need[@]}"; do
      printf 'server {\n    listen 80;\n    listen [::]:80;\n    server_name %s;\n    server_tokens off;\n' "$name"
      printf '    location ^~ /.well-known/acme-challenge/ { root /var/www/letsencrypt; default_type text/plain; try_files $uri =404; }\n'
      printf '    location / { return 404; }\n}\n'
    done
  } > "$boot"
  ln -sf "$boot" /etc/nginx/sites-enabled/sinko-dist-bootstrap.conf
  if ! nginx -t; then rm -f /etc/nginx/sites-enabled/sinko-dist-bootstrap.conf "$boot"; die "nginx -t failed on the temporary file; nothing else changed"; fi
  systemctl reload nginx
  for name in "${need[@]}"; do
    certbot certonly --webroot -w /var/www/letsencrypt -d "$name" --non-interactive --keep-until-expiring \
      --deploy-hook "systemctl reload nginx" \
      || { rm -f /etc/nginx/sites-enabled/sinko-dist-bootstrap.conf "$boot"; systemctl reload nginx; die "certbot failed for $name"; }
  done
  rm -f /etc/nginx/sites-enabled/sinko-dist-bootstrap.conf "$boot"
fi
echo "ok: $(ls -d /etc/letsencrypt/live/updates.ioai.bh /etc/letsencrypt/live/sinko.ioai.bh | tr '\n' ' ')"

say "3. nginx sites"
for name in "${NAMES[@]}"; do
  install -m 0644 "$SRC/$name.conf" "/etc/nginx/sites-available/$name.conf"
  ln -sf "/etc/nginx/sites-available/$name.conf" "/etc/nginx/sites-enabled/$name.conf"
done
if ! nginx -t; then
  for name in "${NAMES[@]}"; do rm -f "/etc/nginx/sites-enabled/$name.conf"; done
  nginx -t && systemctl reload nginx
  die "nginx -t failed with the new sites; they were taken out again and nginx is as before"
fi
systemctl reload nginx
echo "ok"

say "4. first sync and timer"
systemctl enable --now sinko-dist-sync.timer
systemctl start sinko-dist-sync.service || true
journalctl -u sinko-dist-sync --no-pager -n 20 -o cat

say "checks"
check() { printf '%-60s %s\n' "$1" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$1")"; }
check https://updates.ioai.bh/api/releases/latest
check https://updates.ioai.bh/releases/latest/download/install.sh
check https://updates.ioai.bh/releases/latest/download/sinko.tar.gz
check https://updates.ioai.bh/releases/latest/download/sinko.tar.gz.sha256
check https://updates.ioai.bh/lists/guard.txt
check https://updates.ioai.bh/lists/services.json
check https://sinko.ioai.bh/
curl -s --max-time 20 https://updates.ioai.bh/api/releases/latest
systemctl list-timers sinko-dist-sync.timer --no-pager | head -3
