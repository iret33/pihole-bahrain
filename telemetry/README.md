# Sinko anonymous counter

A tiny [Cloudflare Worker](https://developers.cloudflare.com/workers/) with a [D1](https://developers.cloudflare.com/d1/)
database. It answers one question for the project, "how many Sinko boxes are in use?", and it only hears from
boxes whose parent switched the counter on. What a box sends and what happens to it is written in plain words in
[`docs/privacy.md`](../docs/privacy.md); the protocol is in
[`docs/maintainers/architecture.md`](../docs/maintainers/architecture.md) ("Optional anonymous counter").

Running your own counter is optional. Sinko works the same without one, and a box whose counter address is empty
never sends anything.

This counter is the only thing Sinko itself ever sends to the project, and it never receives the names of the sites a
family looks up. (The Pi-hole on the box does pass the names it cannot answer itself to its upstream DNS service,
Cloudflare for Families by default, as every DNS filter does. That service is not this counter and not the project:
see [`docs/privacy.md`](../docs/privacy.md).)

## Deploy in five minutes

You need a free Cloudflare account and Node.js 20 or newer.

```bash
cd telemetry
npx wrangler login

# 1. Create the database. It prints a database_id.
npx wrangler d1 create sinko-counter

# 2. Paste that database_id into wrangler.toml (replace REPLACE-WITH-YOUR-DATABASE-ID).
#    It is an identifier, not a secret.

# 3. Create the two tables (safe to run again at any time).
npx wrangler d1 execute sinko-counter --remote --file=schema.sql

# 4. Deploy. The last line is the address, https://sinko-counter.<your-name>.workers.dev
npx wrangler deploy

# 5. Try it.
curl https://sinko-counter.<your-name>.workers.dev/v1/stats
```

Then tell the parts of Sinko where it is:

* **Boxes**: put `SINKO_TELEMETRY_URL=https://sinko-counter.<your-name>.workers.dev` in `/etc/sinko/config`, or set the
  built-in `TELEMETRY_URL` constant near the top of `bin/sinko` before you build a release (the ready-made image is
  made from such a build). The address must be `https`.
* **Website**: `statsUrl` in `site/config.js` (same address, no trailing slash).
* **README badges**: `https://img.shields.io/endpoint?url=https://<address>/badge/online.json` (also `total.json`
  and `downloads.json`).

Optional, for the download total: GitHub only lets anonymous callers ask 60 times an hour from one address, and
Cloudflare's addresses are shared by many. A token that can read public repositories (a fine-grained token with no
extra permissions is enough) avoids that:

```bash
npx wrangler secret put GITHUB_TOKEN
```

Set it by hand like this. It is not the `GITHUB_TOKEN` that GitHub Actions hands to workflows, and it is never
written to a file or a log.

### Deploying from GitHub Actions

`.github/workflows/deploy-telemetry.yml` runs the tests, applies `schema.sql` and deploys, when you start it by hand
(Actions, "Deploy the counter", Run workflow). It needs two repository secrets, `CLOUDFLARE_API_TOKEN` (a token with
"Edit Cloudflare Workers" and "D1 Edit" permissions) and `CLOUDFLARE_ACCOUNT_ID`. It stops with a clear message while
`database_id` in `wrangler.toml` is still the placeholder.

### Custom domain (optional)

The `workers.dev` address works at once. To use a name of your own, with the domain on Cloudflare, add this to
`wrangler.toml` and deploy again:

```toml
[[routes]]
pattern = "counter.example.org"
custom_domain = true
```

A custom domain also makes Cloudflare's Cache API work. Cloudflare documents working cache operations for custom
domains, so on `workers.dev` assume the Cache API does nothing. The Worker does not depend on it: the counts and the
download total are also remembered in the database for a few minutes (see "Numbers and windows").

## What it does

| Request | Answer |
|---|---|
| `POST /v1/ping` with `{"id","v","hw"}` | `{"online","total"}`. Only from boxes. No CORS. |
| `POST /v1/forget` with `{"id"}` | `{"forgotten":true}`. Deletes the box's record. Only from boxes. No CORS. |
| `GET /v1/stats` | `{"online","active7d","total","countries","versions":{},"hw":{},"downloads","generatedAt"}`, CORS `*`, `Cache-Control: public, max-age=60` |
| `GET /badge/online.json`, `/badge/total.json`, `/badge/downloads.json` | [shields.io endpoint](https://shields.io/badges/endpoint-badge) JSON, CORS `*`, `Cache-Control: public, max-age=300` |
| `OPTIONS` | `204`; CORS headers on the three public paths, none on `/v1/ping` and `/v1/forget` |
| `GET /` | one plain-text paragraph saying what this is |
| cron, daily at 03:17 UTC | deletes boxes unseen for 180 days |

A ping must be `Content-Type: application/json`, at most 512 bytes, with exactly the three fields `id` (32 lowercase
hex characters), `v` (a semantic version such as `3.0.0` or `3.1.0-rc.1`) and `hw` (`orangepi-zero3`, `raspberrypi`,
`x86` or `other`). Anything else is refused, on purpose: the promise to families is that nothing else is sent, so a
body that carries more is a bug in the sender that should be loud.

### Forgetting a box

When a parent switches the counter off (in the page, or with `sudo sinko telemetry off` on an own install), the box
sends `POST /v1/forget` with the body `{"id": "<the same 32 hex characters>"}` and `Content-Type: application/json`,
and then deletes its own copy of the id. The rules are those of a ping: at most 512 bytes, exactly one field
(`id`; a body that carries anything else is refused with `unexpected_field`), the id must be 32 lowercase hex
characters, no CORS, nothing logged about the request.

* The answer is `200 {"forgotten": true}` **whether or not the id was known**, and again on a repeat. It does not tell
  anyone whether an id exists, and a box that did not see the answer can simply ask again.
* The row is deleted, with everything in it (first and last time seen, version, kind of box, country). A later ping
  with the same id would start a new row (the 10-minute repeat rule looks at the row, and the row is gone), but a box
  that was switched off does not ping.
* The public numbers stop including the box at once: the remembered counts are dropped when, and only when, a row was
  really deleted, so a stream of forget requests for ids nobody knows costs nothing and cannot force recounts. (That is
  two statements, the row first. If only the second fails, the box gets a `503`, asks again, gets `200` because the row
  is already gone, and the public counts catch up within their 5 minutes. Do not turn it into one batch without teaching
  the fake database in `test/support` the new statement.)
* Anything other than `200 {"forgotten": true}` (an old Worker that has no `/v1/forget` answers `404`, a database
  that is down answers `503`) makes the box **keep its id, send no pings, and ask again about every 6 hours** until it
  is told. **Deploy this Worker before boxes with the new version reach families**, or a parent who switches the counter
  off will see the box keep trying. The 180-day purge below is the backstop.

Errors are `{"error": "<code>"}` with the status: `400` (`bad_json`, `bad_body`, `invalid_id`, `invalid_version`,
`invalid_hw`, `unexpected_field`, `bad_encoding`, `bad_length`), `404`, `405`, `413`, `415`, `503` (`unavailable`: the
database could not be reached, try later), `500` (`not_configured`, `internal`). A ping that is ignored as a repeat
still answers `200`: the box treats any other status as a failure and retries.

`generatedAt` is an ISO 8601 UTC time: when the counts were worked out.

### Numbers and windows

| Name | Meaning |
|---|---|
| dedupe | a ping from an id seen less than 10 minutes ago is ignored (changes nothing) |
| `online` | seen in the last 12 hours (boxes ping every 6 hours, so one missed ping does not drop a box) |
| `active7d` | seen in the last 7 days |
| `total` | seen in the last 180 days, which is everything that is kept |
| `versions`, `hw` | how the boxes seen in the last 7 days divide (they add up to `active7d`); `versions` lists the 20 most common and puts the rest under `other`, so a flood of made-up versions cannot make the answer grow |
| `countries` | how many different countries the boxes seen in the last 7 days are in. It is a number on purpose: a per-country list would show a "country" with one box in it |
| `downloads` | see below; `null` when unknown |

Edges are inclusive (a box seen exactly 12 hours ago is online) except the purge, which deletes boxes seen *more*
than 180 days ago. The counts are worked out from one database statement, so `online <= active7d <= total` always
holds, and they are kept for 5 minutes: recomputing them for every ping and every visit to the website would read the
whole table each time. So a box that has just pinged may see a number that is a few minutes old, except that the
answer to a ping always counts the box that is asking. If the counts were read but the database refuses to remember them
(a write limit, a full database), they are still answered: the remembered copy is only an optimisation, so the website and
the badges keep working while pings fail.

### Downloads

`downloads` is the sum of GitHub's `download_count` over every asset of every published (not draft) release of
`GITHUB_REPO`. A fresh install downloads up to three files (`install.sh`, `sinko.tar.gz`, `sinko.tar.gz.sha256`) and
every update downloads two, so this counts file downloads, not people. It is cached for 15 minutes. Any failure (a
rate limit, GitHub being down, a surprising answer, more releases than the 1000 that are read) makes it `null`:
never a partial sum, never an old number presented as new. A failure is remembered for one minute so that an outage
is not hammered. A revoked `GITHUB_TOKEN` is noticed (GitHub answers 401) and the request is repeated without it.

## What it stores

One row per box in D1, from `schema.sql`:

| Column | Value |
|---|---|
| `id` | the random 128-bit code the box made for itself (32 hex characters); derived from nothing on the box |
| `first_seen`, `last_seen` | epoch seconds |
| `version`, `hw` | what the box said |
| `country` | two letters from Cloudflare's `CF-IPCountry` header, or empty |

Nothing else. The Worker never reads, stores or logs the sender's address (`CF-Connecting-IP`), user agent or any other
header, and it logs no request: `wrangler.toml` switches Workers Logs off, because new Workers log every request by
default. What the Worker itself writes to the log is a fixed sentence (for example "database error") and, for a
database error, the first 160 characters of the error text.

Cloudflare, like any host, sees the connection that reaches it. That is Cloudflare's own handling under its own
terms and not something this code can change; the Worker does not keep it. Boxes are deleted 180 days after they were
last seen. [`docs/privacy.md`](../docs/privacy.md) says all of this to parents.

### Delete everything

* One box: the parent switches the counter off (the page, *My box*, *Count this box*, or `sudo sinko telemetry off` on an
  own install), and the box deletes its own record through `/v1/forget`. Nobody needs to write to you and no shell
  is needed, which matters because a ready-made box has no login. If the box could not reach the counter at that
  moment it keeps asking. By hand, if you ever have to (you need the id, which only an own install can print with
  `sudo sinko telemetry payload`):
  `npx wrangler d1 execute sinko-counter --remote --command "DELETE FROM installs WHERE id = '<id>'"`.
* Cloudflare may keep restore points of a D1 database (its "Time Travel" feature) for a limited time after a deletion.
  Only the account that owns the database can use them, and the privacy statement says so to parents.
* All boxes: `npx wrangler d1 execute sinko-counter --remote --command "DELETE FROM installs; DELETE FROM meta;"`
* The whole service: `npx wrangler delete` (the Worker) and `npx wrangler d1 delete sinko-counter` (the database).

## Abuse

The counter is a vanity number, not a security control. Do not use it for licensing, billing or any decision that
matters.

Anyone who knows the address can send well-formed pings with made-up ids and make `online` and `total` bigger. Nothing
secret can prevent it: the box is open-source software that runs on hardware its owner controls, so any secret it
held would be public. What limits the damage:

* A ping can only say three things, in a fixed shape; a made-up id is one made-up box, and a repeat within 10 minutes
  counts for nothing.
* Made-up boxes disappear: from `online` 12 hours after the last ping, from `total` after 180 days, and the daily
  purge deletes them. Storage is therefore bounded by what an attacker keeps sending.
* The public answer cannot be made to grow (the version list is capped).
* `/v1/ping` sends no CORS headers, so a web page cannot make its visitors' browsers count themselves. (A script on a
  computer still can.)
* On Cloudflare's free plan, the daily limits on requests and database writes are hard stops: a flood makes pings
  fail instead of costing money. On a paid plan a flood can cost money.
* **A per-address rate limit needs a custom domain.** The Worker cannot rate-limit by address, because it never sees or
  keeps one. Cloudflare's WAF rate limiting rules can do it in front of the Worker (Security, WAF, Rate limiting
  rules, a rule for `URI Path equals /v1/ping` that blocks an address after, say, 30 requests a minute), but those
  rules belong to a **zone** that your Cloudflare account owns. A `*.workers.dev` address is not one. With the
  default `workers.dev` setup there is **no** per-address limit at all: the only protection is the plan's hard
  limits above. If the number on the website matters to you, use the "Custom domain" setup, then add the rule (cover
  `/v1/forget` as well). Do not count on a Worker-side limit: the Worker has no address to key it on.

If the numbers ever look wrong, look at the table:
`npx wrangler d1 execute sinko-counter --remote --command "SELECT version, hw, COUNT(*) FROM installs GROUP BY 1, 2 ORDER BY 3 DESC"`.

## Cost

On the free plans one box costs about four pings a day, each a request, one row read and about two rows written
(the row and its index entry). That is comfortable for a few thousand boxes (the limits are 100,000 requests and 100,000 rows
written a day, and 5 million rows read a day, which is what the 5-minute counts spend most of). Limits and prices change:
check Cloudflare's current D1 and Workers pages before you promise anything.

## Developing

```bash
cd telemetry
npm test                                  # Node 20 or newer, nothing to install
```

The tests run every scenario twice: on a small hand-written fake of the D1 API (works on Node 20) and, when Node has
`node:sqlite` (22.5 or newer), on a real SQLite with `schema.sql` applied exactly as D1 would apply it. The fake does
not parse SQL, so the second run is what proves that the SQL is valid and that the fake means the same thing.

`npm test` names the test files instead of the folder (`node --test test/`): since Node 22 that form fails with
"Cannot find module .../test", and on Node 20 it also runs the helper files as tests.

To run the Worker itself locally (the runtime is `workerd`, as in production):

```bash
npx wrangler d1 execute DB --local --file=schema.sql
npx wrangler dev --local --test-scheduled
curl -X POST -H 'Content-Type: application/json' \
  -d '{"id":"0123456789abcdef0123456789abcdef","v":"3.0.0","hw":"x86"}' http://localhost:8787/v1/ping
curl 'http://localhost:8787/cdn-cgi/handler/scheduled'     # runs the daily purge
```

A Worker's entry module (`src/index.js`) may export nothing but its handlers, or `workerd` refuses to start. Keep
helpers and constants in the other files.
