#!/usr/bin/env bash
# register.sh — register the 16 parental-control block lists as URL-based
# Pi-hole adlists pointing at this repo's raw URLs.
#
# Why URL-based? Pi-hole re-fetches URL adlists on every gravity run (weekly by
# default, or `pihole -g`). So: edit a list here on GitHub, then run
# `pihole -g` on the Pi-hole (or wait for the weekly gravity) and every install
# picks up the change remotely — no need to re-run this script.
#
# Idempotent: safe to re-run. Already-registered lists and an existing "Kids"
# group are skipped.
#
# Requirements:
#   - root (reads /etc/pihole/cli_pw for API auth)
#   - python3 (JSON parsing; always present on a Pi-hole host)
#   - the repo must be PUBLIC for the raw.githubusercontent.com URLs to resolve
set -euo pipefail

RAW="https://raw.githubusercontent.com/iret33/pihole-bahrain/master/lists"
BASE="https://127.0.0.1/api"
LISTS="chatgpt discord facebook instagram microsoft-teams netflix playstation roblox snapchat steam telegram tiktok whatsapp x-twitter xbox youtube"

if [ "$(id -u)" -ne 0 ]; then
  echo "This script needs root (to read /etc/pihole/cli_pw). Re-run with sudo." >&2
  exit 1
fi
command -v python3 >/dev/null 2>&1 || { echo "python3 is required." >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "curl is required." >&2; exit 1; }

# 1. Authenticate to the local Pi-hole API.
PW="$(cat /etc/pihole/cli_pw 2>/dev/null || true)"
[ -n "$PW" ] || { echo "ERROR: /etc/pihole/cli_pw not found. Is Pi-hole v6 installed?" >&2; exit 1; }
SID="$(curl -sk -X POST "$BASE/auth" -H 'Content-Type: application/json' \
  -d "{\"password\":\"$PW\"}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["session"]["sid"])')"

# 2. Ensure a "Kids" group exists (the service lists are scoped to it).
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

# 3. Register each list (skip any whose URL is already present).
EXISTING="$(curl -sk "$BASE/lists" -H "sid: $SID")"
for name in $LISTS; do
  url="$RAW/$name.txt"
  if printf '%s' "$EXISTING" | grep -qF "\"address\":\"$url\""; then
    echo "  skip (already registered): $name"
    continue
  fi
  curl -sk -X POST "$BASE/lists?type=block" -H "sid: $SID" -H 'Content-Type: application/json' \
    -d "{\"address\":[\"$url\"],\"comment\":\"$name\",\"groups\":[$KIDS_ID]}" >/dev/null
  echo "  registered: $name"
done

# 4. Rebuild gravity so the lists are fetched and take effect.
curl -sk -X POST -H "sid: $SID" "$BASE/action/gravity" -o /dev/null
echo "Gravity update triggered — the lists will block once it finishes."
