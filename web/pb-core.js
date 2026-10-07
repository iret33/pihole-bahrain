/* pb-core.js: the pure logic behind the live picture on the parent page. No DOM in here.
 *
 *   - classify Pi-hole's query statuses (blocked / allowed / answered from the box's memory / not final yet)
 *   - name the app behind a domain (longest suffix, from pb/domains.json) and tidy unknown domains
 *   - follow /api/queries without repeating or missing anything, using the SERVER's clock
 *   - decide which queries are worth animating (a home makes many per second; the picture shows a few)
 *   - curve maths for the wires
 *   - the shared state: parseState / defaultState (the same rules as the scheduler, see the contract test)
 *
 * Loaded by the page before pb-live.js, and by node for tests/pb-core.test.js. ES5 on purpose.
 */
(function (root) {
  'use strict';

  // ------------------------------------------------------------------ statuses
  // FTL's query status strings (get_query_status_str in FTL's datastructure.c).
  var BLOCKED = { GRAVITY: 1, REGEX: 1, DENYLIST: 1, EXTERNAL_BLOCKED_IP: 1, EXTERNAL_BLOCKED_NULL: 1, EXTERNAL_BLOCKED_NXRA: 1,
    EXTERNAL_BLOCKED_EDE15: 1, GRAVITY_CNAME: 1, REGEX_CNAME: 1, DENYLIST_CNAME: 1, DBBUSY: 1, SPECIAL_DOMAIN: 1 };
  var ALLOWED = { FORWARDED: 1, RETRIED: 1, RETRIED_DNSSEC: 1 };
  var MEMORY = { CACHE: 1, CACHE_STALE: 1 };

  /** 'blocked' | 'allowed' (sent on to the internet) | 'memory' (answered by the box itself) | 'pending' (not final). */
  function classify(status) {
    var s = String(status || '').toUpperCase();
    if (BLOCKED[s]) return 'blocked';
    if (MEMORY[s]) return 'memory';
    if (ALLOWED[s]) return 'allowed';
    return 'pending';                                   // IN_PROGRESS, UNKNOWN, or a status a newer FTL adds
  }

  // ------------------------------------------------------------------ names
  function isHiddenDomain(d) { d = String(d || '').toLowerCase(); return d === '' || d === 'hidden' || d === '<hidden>'; }
  function isHiddenClient(ip) { return !ip || ip === '0.0.0.0' || ip === '::'; }

  var TWO_LEVEL = /^(com|net|org|gov|edu|co|ac)\.[a-z]{2}$/;
  /** 'r4---sn-x.googlevideo.com' -> 'googlevideo.com'; 'www.bahrain.com.bh' -> 'bahrain.com.bh'. */
  function baseDomain(domain) {
    var parts = String(domain || '').toLowerCase().replace(/\.$/, '').split('.');
    if (parts.length <= 2) return parts.join('.');
    var last2 = parts.slice(-2).join('.');
    return TWO_LEVEL.test(last2) ? parts.slice(-3).join('.') : last2;
  }

  /** The app id for a domain by its longest matching suffix, or null. `map` is pb/domains.json's "domains" object. */
  function appFor(domain, map) {
    var d = String(domain || '').toLowerCase().replace(/\.$/, '');
    while (d) {
      if (map && Object.prototype.hasOwnProperty.call(map, d)) return map[d];
      var dot = d.indexOf('.');
      if (dot < 0) return null;
      d = d.slice(dot + 1);
    }
    return null;
  }

  // ------------------------------------------------------------------ numbers
  function formatCount(n, locale) {
    n = Math.round(Number(n) || 0);
    try { return new Intl.NumberFormat(locale || 'en-GB').format(n); } catch (e) { return String(n); }
  }
  /** Percent of `part` in `whole`: 0 when nothing happened, one decimal below 10. */
  function percent(part, whole) {
    if (!whole) return 0;
    var p = 100 * part / whole;
    return p < 10 ? Math.round(p * 10) / 10 : Math.round(p);
  }

  // ------------------------------------------------------------------ following the query log
  /**
   * Turns successive /api/queries responses into new events, once each.
   *
   *   var feed = new Feed(); var url = feed.url();       // first call reads a few recent rows, only to remember them
   *   feed.ingest(response)                                // the first response only primes it: no events
   *   ... url = feed.url(); events = feed.ingest(response) // later: only queries not seen before, oldest first
   *
   * `from` comes from the newest `time` in the server's own responses, never from the phone's clock, which may differ.
   * Rows that are not final yet (IN_PROGRESS) are skipped WITHOUT being remembered, because FTL returns them again
   * later with their final status.
   */
  function Feed(options) {
    options = options || {};
    this.length = options.length || 60;
    this.keep = options.keep || 800;
    this.overlap = options.overlap === undefined ? 2 : options.overlap;     // seconds re-read on every poll
    this.seen = {};
    this.order = [];
    this.from = null;
    this.primed = false;
    this.privacy = false;        // true: the box hides queries (privacy level 3) or has none to show
    this.truncated = false;      // true: a poll came back full, so some queries were probably not shown
  }
  Feed.prototype.url = function () {
    // The first call reads a few recent rows just to remember them (so they are never replayed as "new").
    return this.from === null ? '/api/queries?length=20' : '/api/queries?from=' + this.from + '&length=' + this.length;
  };
  Feed.prototype._remember = function (id) {
    this.seen[id] = 1;
    this.order.push(id);
    while (this.order.length > this.keep) delete this.seen[this.order.shift()];
  };
  Feed.prototype.ingest = function (resp, serverNow) {
    var rows = resp && resp.queries instanceof Array ? resp.queries : [];
    this.privacy = rows.length === 0 && resp && resp.cursor === null;
    var newest = null, fresh = [], i, r;
    for (i = 0; i < rows.length; i++) {
      r = rows[i];
      if (typeof r.time === 'number' && (newest === null || r.time > newest)) newest = r.time;
    }
    // A row stamped later than the box's own clock (it stepped back after a wrong boot time) must not pin `from` in the future.
    var cap = typeof serverNow === 'number' && serverNow > 0 ? serverNow : Infinity;
    if (newest !== null) this.from = Math.max(0, Math.floor(Math.min(newest, cap) - this.overlap));
    else if (this.from === null && resp) this.from = Math.floor(Number(resp.time) || 0) || null;
    if (!this.primed) {                                   // the first answer only tells us where "now" is
      this.primed = true;
      for (i = 0; i < rows.length; i++) if (classify(rows[i].status) !== 'pending') this._remember(rows[i].id);
      if (this.from === null) this.from = 0;
      return [];
    }
    this.truncated = rows.length >= this.length;
    for (i = 0; i < rows.length; i++) {
      r = rows[i];
      if (this.seen[r.id] || classify(r.status) === 'pending') continue;
      this._remember(r.id);
      fresh.push({
        id: r.id, time: r.time, kind: classify(r.status), status: r.status,
        domain: r.domain, hiddenDomain: isHiddenDomain(r.domain),
        ip: r.client && r.client.ip, name: r.client && r.client.name, hiddenClient: isHiddenClient(r.client && r.client.ip),
      });
    }
    fresh.sort(function (a, b) { return a.time - b.time || a.id - b.id; });
    return fresh;
  };

  // ------------------------------------------------------------------ deciding what to animate
  /** Higher = more worth showing: blocked, known apps and children's devices come first; memory answers last. */
  function priority(ev) {
    return (ev.kind === 'blocked' ? 4 : 0) + (ev.app ? 3 : 0) + (ev.child ? 2 : 0) + (ev.kind === 'memory' ? -1 : 0);
  }

  /**
   * Hands out queued events at a gentle pace and never lets the queue grow: when it is full, the least interesting
   * event is dropped (it still counts in the numbers, it just does not get its own packet).
   */
  function Pacer(options) {
    options = options || {};
    this.gap = options.gap === undefined ? 380 : options.gap;    // ms between packets: about 2.6 per second at most
    this.max = options.max || 10;
    this.queue = [];
    this.last = -1e12;
    this.dropped = 0;
  }
  Pacer.prototype.push = function (events) {
    var i, j, worst;
    for (i = 0; i < events.length; i++) {
      this.queue.push(events[i]);
      if (this.queue.length > this.max) {
        worst = 0;
        for (j = 1; j < this.queue.length; j++) if (priority(this.queue[j]) < priority(this.queue[worst])) worst = j;
        this.queue.splice(worst, 1);
        this.dropped++;
      }
    }
  };
  /** The next event to animate at time `now` (ms), or null if it is too soon or nothing waits. */
  Pacer.prototype.next = function (now) {
    if (!this.queue.length || now - this.last < this.gap) return null;
    var best = 0, i;
    for (i = 1; i < this.queue.length; i++) if (priority(this.queue[i]) > priority(this.queue[best])) best = i;
    this.last = now;
    return this.queue.splice(best, 1)[0];
  };

  // ------------------------------------------------------------------ curves for the wires
  function lerp(a, b, t) { return a + (b - a) * t; }
  function easeInOut(t) { return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; }
  function clamp01(t) { return t < 0 ? 0 : t > 1 ? 1 : t; }

  /** A smooth S-curve from a to b ({x, y}); it leaves along the longer axis, so wires look right in any layout. */
  function wire(a, b) {
    var horizontal = Math.abs(b.x - a.x) >= Math.abs(b.y - a.y);
    var c1 = horizontal ? { x: lerp(a.x, b.x, 0.5), y: a.y } : { x: a.x, y: lerp(a.y, b.y, 0.5) };
    var c2 = horizontal ? { x: lerp(a.x, b.x, 0.5), y: b.y } : { x: b.x, y: lerp(a.y, b.y, 0.5) };
    var r = function (n) { return Math.round(n * 10) / 10; };
    return { p0: a, p1: c1, p2: c2, p3: b,
      d: 'M' + r(a.x) + ' ' + r(a.y) + 'C' + r(c1.x) + ' ' + r(c1.y) + ' ' + r(c2.x) + ' ' + r(c2.y) + ' ' + r(b.x) + ' ' + r(b.y) };
  }
  /**
   * A right-angled wire from a to b, the 8-bit way: along `axis` ('v' or 'h'; the longer one when not given) to the halfway
   * line, across, then on to b. Wires
   * from a row of devices to one box meet on the same halfway line, so they read as one bus into one input.
   */
  function elbow(a, b, axis) {
    var vertical = axis ? axis === 'v' : Math.abs(b.y - a.y) >= Math.abs(b.x - a.x), mid, pts = [a], lens = [], total = 0, i, l;
    if (vertical) { mid = lerp(a.y, b.y, 0.5); pts.push({ x: a.x, y: mid }, { x: b.x, y: mid }); }
    else { mid = lerp(a.x, b.x, 0.5); pts.push({ x: mid, y: a.y }, { x: mid, y: b.y }); }
    pts.push(b);
    var r = function (n) { return Math.round(n * 10) / 10; }, d = 'M' + r(a.x) + ' ' + r(a.y);
    for (i = 1; i < pts.length; i++) {
      l = Math.abs(pts[i].x - pts[i - 1].x) + Math.abs(pts[i].y - pts[i - 1].y);
      lens.push(l); total += l;
      d += 'L' + r(pts[i].x) + ' ' + r(pts[i].y);
    }
    return { pts: pts, lens: lens, total: total, d: d };
  }
  /** The point at fraction t (0..1) along a wire() curve or an elbow() line. */
  function pointAt(w, t) {
    t = clamp01(t);
    if (w.pts) {
      var want = t * w.total, i, f;
      for (i = 0; i < w.lens.length; i++) {
        if (want <= w.lens[i] || i === w.lens.length - 1) {
          f = w.lens[i] ? Math.min(1, want / w.lens[i]) : 1;
          return { x: lerp(w.pts[i].x, w.pts[i + 1].x, f), y: lerp(w.pts[i].y, w.pts[i + 1].y, f) };
        }
        want -= w.lens[i];
      }
      return { x: w.pts[w.pts.length - 1].x, y: w.pts[w.pts.length - 1].y };
    }
    var u = 1 - t, a = u * u * u, b = 3 * u * u * t, c = 3 * u * t * t, d = t * t * t;
    return { x: a * w.p0.x + b * w.p1.x + c * w.p2.x + d * w.p3.x, y: a * w.p0.y + b * w.p1.y + c * w.p2.y + d * w.p3.y };
  }

  // ------------------------------------------------------------------ the numbers
  /**
   * The most stopped things, by app: domains from /api/stats/top_domains?blocked=true are grouped by the app they belong to
   * (googlevideo.com and youtube.com are both "youtube"); domains of no known app are counted together as `other`, never
   * guessed to be "ads" and never shown as raw hostnames. Returns [{ app, count }] biggest first (`other` has app: null).
   */
  function topByApp(domains, map, limit, only) {
    var tally = {}, other = 0, i, d, app, out = [], allowed = null;
    if (only) { allowed = {}; only.forEach(function (id) { allowed[id] = true; }); }
    for (i = 0; i < (domains || []).length; i++) {
      d = domains[i];
      if (!d || isHiddenDomain(d.domain)) continue;
      app = appFor(d.domain, map);
      // `only`: the apps that are blocked for children. A tracker on netflix.com is blocked by an ad list, not by "blocking Netflix".
      if (app && (!allowed || allowed[app])) tally[app] = (tally[app] || 0) + (d.count || 0); else other += d.count || 0;
    }
    Object.keys(tally).forEach(function (k) { out.push({ app: k, count: tally[k] }); });
    out.sort(function (a, b) { return b.count - a.count; });
    if (typeof limit === 'number') out = out.slice(0, limit);
    if (other) out.push({ app: null, count: other });          // everything else, always shown last and never cut off
    return out;
  }

  /**
   * /api/history (144 slots of 10 minutes) as 24 hourly buckets, oldest first: [{ t, total, blocked }].
   * Buckets count back from the newest slot, so the phone's clock and time zone do not matter.
   */
  function hourly(history) {
    var slots = history && history.history instanceof Array ? history.history : [];
    var out = [], i, k, b;
    for (i = 0; i < 24; i++) out.push({ t: 0, total: 0, blocked: 0 });
    if (!slots.length) return out;
    var last = slots[slots.length - 1].timestamp;
    for (i = 0; i < slots.length; i++) {
      k = 23 - Math.floor((last - slots[i].timestamp) / 3600);
      if (k < 0 || k > 23) continue;
      b = out[k];
      if (!b.t || slots[i].timestamp < b.t) b.t = slots[i].timestamp;
      b.total += slots[i].total || 0;
      b.blocked += slots[i].blocked || 0;
    }
    return out;
  }

  // ------------------------------------------------------------------ is each child's device protected?
  /**
   * What the picture shows for one child's device, in this order of importance:
   *   overridden  another Pi-hole rule overrides this child's rules (NOT protected)
   *   unknown     the box cannot tell when devices were last online (privacy setting, or the device list failed): "rules set",
   *               never "not seen" (a guess that would worry a parent for nothing). Pause and internet-off are still known.
   *   unreachable it has not asked the box anything for 24 h, or the box has never seen it (probably not using the box)
   *   offline     the internet is switched off (the big button, bedtime or an offline break)
   *   paused      this device was paused
   *   active      it asked something in the last 5 minutes
   *   quiet       nothing right now
   * `serverNow` and `lastQuery` are seconds on the BOX's clock; lastQuery 0/undefined means never.
   */
  function deviceState(d) {
    if (d.shadowed) return 'overridden';
    if (d.unknown) return d.offline ? 'offline' : d.paused ? 'paused' : d.recent ? 'active' : 'unknown';
    if (!d.lastQuery || d.serverNow - d.lastQuery > 24 * 3600) return d.recent ? 'active' : 'unreachable';
    if (d.offline) return 'offline';
    if (d.paused) return 'paused';
    return d.recent || d.serverNow - d.lastQuery < 300 ? 'active' : 'quiet';
  }

  // ------------------------------------------------------------------ polling
  /**
   * Runs named tasks on their own schedules: each starts right away (staggered a little), then repeats `every` ms
   * AFTER the previous run finished, so requests never pile up. A failing task backs off (x2 each time, up to 30 s) and
   * recovers by itself. Nothing runs while the page is hidden; when it becomes visible again, tasks that are due run at once.
   *
   *   var poller = new Poller({ tasks: [{ name: 'queries', every: 3500, run: function () { return promise; } }],
   *                             onError: function (name, err, fails) {}, visible: function () { return true; } });
   *   poller.start(); poller.wake();   // wake() after visibilitychange
   *   poller.stop();
   *
   * setTimeout/clearTimeout/now/visible can be replaced, which is how the tests drive it with a fake clock.
   */
  function Poller(options) {
    this.tasks = (options.tasks || []).map(function (t) {
      return { name: t.name, every: t.every, run: t.run, fails: 0, timer: null, due: 0, running: false };
    });
    this.onError = options.onError || function () {};
    this.onOk = options.onOk || function () {};
    this.visible = options.visible || function () { return true; };
    this.now = options.now || function () { return new Date().getTime(); };
    this.setTimeout = options.setTimeout || function (f, ms) { return setTimeout(f, ms); };
    this.clearTimeout = options.clearTimeout || function (id) { clearTimeout(id); };
    this.maxBackoff = options.maxBackoff || 30000;
    this.stagger = options.stagger === undefined ? 250 : options.stagger;
    this.active = false;
  }
  Poller.prototype.start = function () {
    var self = this;
    this.active = true;
    this.tasks.forEach(function (t, i) { self._plan(t, i * self.stagger); });
  };
  Poller.prototype.stop = function () {
    var self = this;
    this.active = false;
    this.tasks.forEach(function (t) { if (t.timer !== null) { self.clearTimeout(t.timer); t.timer = null; } });
  };
  /** Call when the page becomes visible again: whatever is due runs now. */
  Poller.prototype.wake = function () {
    var self = this, now = this.now();
    if (!this.active || !this.visible()) return;
    this.tasks.forEach(function (t) {
      if (!t.running && t.due <= now) { if (t.timer !== null) self.clearTimeout(t.timer); self._plan(t, 0); }
    });
  };
  Poller.prototype._plan = function (t, delay) {
    var self = this;
    t.due = this.now() + delay;
    if (t.timer !== null) this.clearTimeout(t.timer);
    t.timer = this.setTimeout(function () { t.timer = null; self._tick(t); }, delay);
  };
  Poller.prototype._tick = function (t) {
    var self = this;
    if (!this.active) return;
    if (!this.visible()) { t.due = 0; return; }              // paused: wake() picks it up again
    t.running = true;
    var done = function (ok, err) {
      t.running = false;
      if (!self.active) return;
      if (ok) { t.fails = 0; self.onOk(t.name); }
      else { t.fails++; self.onError(t.name, err, t.fails); }
      self._plan(t, ok ? t.every : Math.min(self.maxBackoff, t.every * Math.pow(2, t.fails)));
    };
    var result;
    try { result = t.run(); } catch (e) { return done(false, e); }
    if (result && typeof result.then === 'function') result.then(function () { done(true); }, function (e) { done(false, e); });
    else done(true);
  };

  // ------------------------------------------------------------------ the shared state
  // The state lives in the description of the "pb-state" group and is written by TWO programs: this page and the scheduler
  // (bin/sinko). Both normalise it with the same rules, and a value one of them does not know is lost on its next write.
  // tests/fixtures/state-cases.json is the executable spec, run against bin/sinko's parse_state by tests/test_state_contract.py
  // and against this function by tests/state-contract.test.js. Change one side and the other has to follow.
  var SEMVER_RE = /^\d{1,4}\.\d{1,4}\.\d{1,4}$/;
  var NOTES_RE = /^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/releases(\/tag\/[A-Za-z0-9_.+-]+)?$/;
  var UPDATE_STATUSES = ['idle', 'running', 'ok', 'failed'];
  var POWER_ACTIONS = ['reboot', 'poweroff'];
  var HHMM_RE = /^\d\d:\d\d$/;
  // "Blank" for update.error: the same characters bin/sinko lists (neither language's own idea of whitespace decides).
  var BLANK_RE = /^[\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]*$/;

  function defaultState() {
    return {
      v: 1, timer: null,
      schedule: { enabled: false, start: '21:00', end: '06:00', days: [0, 1, 2, 3, 4, 5, 6] },
      scheduleActive: false,
      update: { auto: false, request: null, checkRequest: null, latest: null, notes: null, checked: 0, status: 'idle',
        from: null, to: null, at: 0, error: null, rolledBack: null },
      power: { request: null, action: null },
      telemetry: { on: null },
      community: null,
      setup: { done: false }
    };
  }

  function isDict(x) { return x !== null && typeof x === 'object' && !(x instanceof Array); }
  function isNum(x) { return typeof x === 'number' && isFinite(x); }
  function isCount(x) { return isNum(x) && x >= 0; }
  /** Python's bool(): the scheduler normalises with it, so an empty list or object is false there. */
  function truthy(x) {
    if (x === null || x === undefined || x === false || x === 0 || x === '') return false;
    if (x instanceof Array) return x.length > 0;
    if (typeof x === 'object') return Object.keys(x).length > 0;
    return true;
  }
  var ASTRAL = /[\uD800-\uDBFF][\uDC00-\uDFFF]/g;
  /** Length in characters, not UTF-16 units (the scheduler counts characters). */
  function charLength(s) { return s.replace(ASTRAL, 'x').length; }
  function charSlice(s, n) {
    if (charLength(s) <= n) return s;
    var out = '', count = 0, i = 0, c;
    while (count < n && i < s.length) {
      c = s.charAt(i);
      if (/[\uD800-\uDBFF]/.test(c) && i + 1 < s.length) { c += s.charAt(i + 1); i++; }
      out += c; count++; i++;
    }
    return out;
  }
  /** A request marker the page writes (the time in ms) and the scheduler acts on once: a positive number or a string of 1-40 characters. */
  function nonce(x) {
    if (isNum(x) && x > 0) return x;
    if (typeof x === 'string' && x.length > 0 && charLength(x) <= 40) return x;
    return null;
  }
  function parseUpdate(raw) {
    var out = defaultState().update;
    if (!isDict(raw)) return out;
    out.auto = raw.auto === true;
    out.request = nonce(raw.request);
    out.checkRequest = nonce(raw.checkRequest);
    ['latest', 'from', 'to'].forEach(function (k) { out[k] = typeof raw[k] === 'string' && SEMVER_RE.test(raw[k]) ? raw[k] : null; });
    out.notes = typeof raw.notes === 'string' && NOTES_RE.test(raw.notes) ? raw.notes : null;
    ['checked', 'at'].forEach(function (k) { out[k] = isCount(raw[k]) ? raw[k] : 0; });
    out.status = UPDATE_STATUSES.indexOf(raw.status) >= 0 ? raw.status : 'idle';
    out.error = typeof raw.error === 'string' && !BLANK_RE.test(raw.error) ? charSlice(raw.error, 200) : null;
    out.rolledBack = typeof raw.rolledBack === 'boolean' ? raw.rolledBack : null;
    return out;
  }
  function parsePower(raw) {
    var out = defaultState().power;
    if (isDict(raw) && POWER_ACTIONS.indexOf(raw.action) >= 0) {
      out.request = nonce(raw.request);
      out.action = out.request === null ? null : raw.action;
    }
    return out;
  }
  function validHHMM(x) { return typeof x === 'string' && HHMM_RE.test(x) && +x.slice(0, 2) <= 23 && +x.slice(3) <= 59; }

  /** Anything (the group description, "", garbage) -> a complete, valid state. Unknown keys are dropped. */
  function parseState(raw) {
    var s = defaultState(), d = null;
    try { d = raw ? JSON.parse(raw) : null; } catch (e) { d = null; }
    if (!isDict(d)) d = {};
    var tm = d.timer;
    if (isDict(tm) && (tm.mode === 'free' || tm.mode === 'block') && isNum(tm.until)) {
      var snap = isDict(tm.snapshot) ? tm.snapshot : {}, services = {};
      if (isDict(snap.services)) Object.keys(snap.services).forEach(function (k) { services[k] = truthy(snap.services[k]); });
      s.timer = { mode: tm.mode, until: tm.until, snapshot: { services: services, offline: truthy(snap.offline) } };
    }
    if (isDict(d.schedule)) {
      var sc = d.schedule;
      s.schedule.enabled = truthy(sc.enabled);
      if (validHHMM(sc.start)) s.schedule.start = sc.start;
      if (validHHMM(sc.end)) s.schedule.end = sc.end;
      if (sc.days instanceof Array) {
        s.schedule.days = sc.days.filter(function (x, i, a) { return typeof x === 'number' && x % 1 === 0 && x >= 0 && x <= 6 && a.indexOf(x) === i; })
          .sort(function (a, b) { return a - b; });
      }
    }
    s.scheduleActive = truthy(d.scheduleActive);
    s.update = parseUpdate(d.update);
    s.power = parsePower(d.power);
    if (isDict(d.telemetry) && typeof d.telemetry.on === 'boolean') s.telemetry = { on: d.telemetry.on };
    if (isDict(d.community) && isCount(d.community.online) && isNum(d.community.at)) {
      s.community = { online: Math.floor(d.community.online), at: d.community.at };
    }
    if (isDict(d.setup)) s.setup = { done: d.setup.done === true };
    return s;
  }

  /**
   * One read-modify-write of the stored description: parse what is stored NOW (never a copy the page kept earlier, the scheduler
   * may have written since), let `mutate(state)` change only what this write is about, normalise again so nothing the schema
   * does not allow can be stored, and return { state, json }. Every field `mutate` does not touch survives, which is the whole
   * point: both programs write the same description, and a field one of them forgets is wiped for the other.
   */
  function editState(raw, mutate) {
    var st = parseState(raw);
    mutate(st);
    st = parseState(JSON.stringify(st));
    return { state: st, json: JSON.stringify(st) };
  }

  var api = {
    parseState: parseState, defaultState: defaultState, editState: editState,
    classify: classify, isHiddenDomain: isHiddenDomain, isHiddenClient: isHiddenClient, baseDomain: baseDomain, appFor: appFor,
    formatCount: formatCount, percent: percent, topByApp: topByApp, hourly: hourly, deviceState: deviceState, Feed: Feed, priority: priority, Pacer: Pacer, Poller: Poller,
    lerp: lerp, easeInOut: easeInOut, clamp01: clamp01, wire: wire, elbow: elbow, pointAt: pointAt,
  };
  root.PBCore = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : this);
