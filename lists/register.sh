#!/usr/bin/env bash
# register.sh [lists-dir] — install the parental-control block lists as
# FILE-based Pi-hole adlists. Private-repo friendly: reads local *.txt files,
# so no public raw URLs and no stored tokens are needed.
#
# Copies *.txt from <lists-dir> (default: this file's directory) into
# /etc/pihole/lists/, registers each as a file:// blocklist in the "Kids" group,
# then runs gravity.
#
# Update flow (repo is private): `git pull` the checkout, then re-run this
# script (or install.sh) to refresh the lists.
#
# Idempotent: safe to re-run; already-registered lists and an existing "Kids"
# group are skipped.
#
# Requirements: root (writes /etc/pihole/lists, reads /etc/pihole/cli_pw),
# python3, curl.
set -euo pipefail

LISTS_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
BASE="https://127.0.0.1/api"
NAMES="chatgpt discord facebook instagram microsoft-teams netflix playstation roblox snapchat steam telegram tiktok whatsapp x-twitter xbox youtube"

if [ "$(id -u)" -ne 0 ]; then
  echo "This script needs root (to write /etc/pihole/lists and read /etc/pihole/cli_pw)." >&2
  exit 1
fi
command -v python3 >/dev/null 2>&1 || { echo "python3 is required." >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "curl is required." >&2; exit 1; }

# 0. Copy the list files into Pi-hole's lists dir.
mkdir -p /etc/pihole/lists
for name in $NAMES; do
  src="$LISTS_DIR/$name.txt"
  [ -f "$src" ] || { echo "ERROR: missing list file $src" >&2; exit 1; }
  cp "$src" "/etc/pihole/lists/$name.txt"
done
chown pihole:pihole /etc/pihole/lists/*.txt 2>/dev/null || chown 999:1001 /etc/pihole/lists/*.txt 2>/dev/null || true
chmod 644 /etc/pihole/lists/*.txt
echo "copied list files into /etc/pihole/lists/"

# 1. Authenticate to the local Pi-hole API.
PW="$(cat /etc/pihole/cli_pw 2>/dev/null || true)"
[ -n "$PW" ] || { echo "ERROR: /etc/pihole/cli_pw not found. Is Pi-hole v6 installed?" >&2; exit 1; }
SID="$(curl -sk -X POST "$BASE/auth" -H 'Content-Type: application/json' \
  -d "{\"password\":\"$PW\"}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["session"]["sid"])')"

# 2. Ensure a "Kids" group exists.
find_group() {
  curl -sk "$BASE/groups" -H "sid: $SID" \
    | python3 -c 'import sys,json; d=json.load(sys.stdin); print(next((str(g["id"]) for g in d.get("groups",[]) if g["name"]=="Kids"), ""))'
}
KIDS_ID="$(find_group)"
if [ -z "$KIDS_ID" ]; then
  curl -sk -X POST "$BASE/groups" -H "sid: $SID" -H 'Content-Type: application/json' \
    -d '{"name":["Kids"],"comment":"Parental-control service blocklists","enabled":true}' >/dev/null
  KIDS_ID="$(find_group)"
fi
if [ -z "$KIDS_ID" ]; then
  echo "WARNING: could not create/find Kids group; falling back to group 0 (Default)." >&2
  KIDS_ID=0
fi
echo "Registering service lists into group id $KIDS_ID"

# 3. Register each list (skip any whose file:// address is already present).
EXISTING="$(curl -sk "$BASE/lists" -H "sid: $SID")"
for name in $NAMES; do
  addr="file:///etc/pihole/lists/$name.txt"
  if printf '%s' "$EXISTING" | grep -qF "\"address\":\"$addr\""; then
    echo "  skip (already registered): $name"
    continue
  fi
  curl -sk -X POST "$BASE/lists?type=block" -H "sid: $SID" -H 'Content-Type: application/json' \
    -d "{\"address\":[\"$addr\"],\"comment\":\"$name\",\"groups\":[$KIDS_ID]}" >/dev/null
  echo "  registered: $name"
done

# 4. Rebuild gravity so the lists take effect.
curl -sk -X POST -H "sid: $SID" "$BASE/action/gravity" -o /dev/null
echo "Gravity update triggered — the lists will block once it finishes."
