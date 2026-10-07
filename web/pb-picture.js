/* pb-picture.js: the live picture on the parent page.
 *
 * Shows the home's internet requests as little 8-bit sprites travelling along right-angled wires: each device asks the
 * family box first, the box says yes or no, an allowed request goes on to the internet, and a refused one falls into the
 * black hole beside the box. Every block has one way in and one way out, so the picture stays calm. The numbers (checked / stopped / share) come from
 * Pi-hole's own statistics. Everything is read-only: this file never changes a rule.
 *
 * Data (all through the page's own signed-in `call`): /api/stats/summary every 15 s, /api/queries every 3.5 s (a few rows,
 * only newer than the last poll), and, only while the detail sheet is open, /api/history and /api/stats/top_domains.
 * Honesty rules: the numbers say "whole home, last 24 hours"; example packets (the tour) are labelled "Example" and never
 * touch a counter; an unknown domain is never named ("a website", or "an unwanted site" when it was stopped); a stop is only
 * credited to an app when that app is blocked for the child (a tracker on an allowed app's domain is not "blocking Netflix");
 * a device is named for a stop only; nothing here ever shows a raw domain name.
 *
 * Everything shown is set with textContent: names and domains come from the network.
 * Depends on pb-core.js and pb-live.js. ES5 on purpose, like the rest of the page.
 */
(function (root) {
  'use strict';

  var C = root.PBCore, L = root.PBLive;
  var doc = root.document;

  // ------------------------------------------------------------------ module state
  var env = null;              // { call, t, locale, serverNow, domains, openSheet, goDevices, addDevice }
  var m = null;                // the view-model from app.js (see app.js picModel())
  var el = {};                 // cached elements
  var stage = null, feed = null, pacer = null, poller = null, io = null;
  var started = false, inView = true, collapsed = false;
  var slots = [];              // what is drawn in the device row: [{ id, kind: 'kid'|'more'|'rest'|'add', kids: [...] }]
  var slotKey = '';
  var ipSlot = {};             // ip -> slot id ('dev-0'...)
  var kidByIp = {};            // ip -> the child's device it belongs to
  var kidState = {};           // kid key -> state name from deviceState
  var lastSeen = {};           // ip -> phone-clock ms of the last real query we saw from it (by the query's own time on the box)
  var summary = null, delta = { total: 0, blocked: 0 }, shown = { total: 0, blocked: 0, share: 0 };
  var fails = { summary: 0, queries: 0 };   // consecutive failed polls per task: two in a row shows "not updating"
  var summaryAt = 0;           // the box clock (s) when the last summary arrived: queries after it are not in it yet
  var blockingOff = false;     // Pi-hole's blocking is switched off: every rule is ignored
  var lastStale = false, lastRecentSig = '';
  var clientsHidden = false;   // privacy level 2+: clients are not named in the log
  var domainsHidden = false;   // privacy level 1+
  var queriesHidden = false;   // privacy level 3
  var recent = [];             // [{ kind, label, who, at (ms), n }]
  var wireUntil = {};          // wire id -> ms until which it looks busy
  var voice = { until: 0, last: 0, timer: 0 };
  var pumpTimer = 0, recentTimer = 0, ariaTimer = 0, recentTick = 0, wireTimer = 0, captionTimer = 0, repaintTimer = 0;
  var captionHeld = false, wired = false, sayFlip = false;
  var tour = { on: false, i: 0, timer: 0 };
  var minute = [];             // [{ at, blocked }] events of the last minute, for the screen-reader summary
  var lastAria = '';
  var detailOpen = false, detail = null;

  var SLOT_MAX = 3;            // kids drawn as their own node (more are folded into "+N")
  function privacyHides() { return clientsHidden || queriesHidden; }       // privacy level 2+: who asked what is not known
  function stale() { return fails.summary >= 2 || fails.queries >= 2; }

  function $(id) { return doc.getElementById(id); }
  function t(key, vars) { return env.t(key, vars); }
  function now() { return new Date().getTime(); }
  function fmt(n) { return C.formatCount(n, env.locale); }
  function fmtTile(n) {                                   // the tiles are narrow: 123456 becomes 123K
    if (n < 100000) return fmt(n);
    try { return new Intl.NumberFormat(env.locale, { notation: 'compact', maximumFractionDigits: 1 }).format(n); } catch (e) { return fmt(n); }
  }
  function node(tag, cls, text) {
    var n = doc.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function icon(id) {
    var s = L.svg('svg', { 'aria-hidden': 'true' });
    s.appendChild(L.svg('use', { href: '#' + id }));
    return s;
  }
  function hasDialog() { return !!doc.querySelector('dialog[open]'); }
  function watching() { return started && !doc.hidden && inView && !hasDialog(); }   // the page is being looked at
  function active() { return watching() && !collapsed; }                              // ... and the picture is open
  function store(val) {                                                               // the open/closed choice is remembered per phone
    try { if (val === undefined) return root.localStorage.getItem('pb.live'); root.localStorage.setItem('pb.live', val); } catch (e) { /* private mode */ }
    return null;
  }

  /** A hung request must turn into a failure, not a picture that silently stops: give up after `ms`. */
  function withTimeout(promise, ms) {
    return new Promise(function (resolve, reject) {
      var timer = root.setTimeout(function () { reject(new Error('timeout')); }, ms);
      promise.then(function (v) { root.clearTimeout(timer); resolve(v); }, function (e) { root.clearTimeout(timer); reject(e); });
    });
  }

  // ------------------------------------------------------------------ app names
  function appInfo(id) {
    var a = m && m.apps && m.apps[id];
    return a || null;
  }
  /**
   * What to call a lookup, as language-free parts (resolved to words when drawn, so a language switch changes old lines too).
   * Never a host name. A stop is credited to an app ONLY when that app is blocked for the child it came from: an ad list also
   * stops trackers on allowed apps' domains, and "Stopped Netflix" would tell a parent Netflix is blocked. While the internet is
   * off or the device is paused, everything is stopped, so the reason is that, not "an unwanted site".
   */
  function labelParts(ev) {
    if (ev.hiddenDomain) return { key: 'lvWebsite' };
    var kid = ev.ip ? kidByIp[ev.ip] : null;
    if (ev.kind === 'blocked') {
      if (kid && m.offline) return { key: 'lvWhyOff' };
      if (kid && kid.paused) return { key: 'lvWhyPaused' };
      if (kid && ev.app && m.blockedApps.indexOf(ev.app) >= 0 && appInfo(ev.app)) return { app: ev.app };
      return { key: 'lvUnwanted' };           // also keeps adult domain names off the screen
    }
    // A grown-up's phone is not under the children's rules: "Yes · Instagram" from it, while Instagram is blocked for the children,
    // would read as "the block does not work". Only a child's own wire may show a blocked app being allowed (that IS the bad news).
    if (ev.app && appInfo(ev.app) && (kid || m.blockedApps.indexOf(ev.app) < 0)) return { app: ev.app };
    return { key: 'lvWebsite' };
  }
  function resolveLabel(p) {
    var a = p.app && appInfo(p.app);
    if (a) return { text: a.name, badge: a.mono, color: a.color };
    return { text: t(p.key || 'lvWebsite'), badge: '', color: '' };
  }
  function labelFor(ev) { return resolveLabel(labelParts(ev)); }

  // ------------------------------------------------------------------ the device row
  function kidName(k) { return k.name || t('lvDevice'); }

  /** Which nodes the device row shows for this model, and which kid / address belongs to which node. */
  function buildSlots(real) {
    var kids = m.kids, out = [], i;
    if (tour.on && !real) return [{ id: 'dev-0', kind: 'example', kids: [] }, { id: 'rest', kind: 'rest', kids: [] }];   // examples never come from a real child
    if (!kids.length) {
      out.push({ id: 'dev-0', kind: 'add', kids: [] });
    } else if (kids.length <= SLOT_MAX) {
      for (i = 0; i < kids.length; i++) out.push({ id: 'dev-' + i, kind: 'kid', kids: [kids[i]] });
    } else {
      for (i = 0; i < SLOT_MAX - 1; i++) out.push({ id: 'dev-' + i, kind: 'kid', kids: [kids[i]] });
      out.push({ id: 'dev-' + (SLOT_MAX - 1), kind: 'more', kids: kids.slice(SLOT_MAX - 1) });
    }
    out.push({ id: 'rest', kind: 'rest', kids: [] });
    return out;
  }

  function stateOf(k) {
    var recentIp = false, i;
    for (i = 0; i < k.ips.length; i++) if (lastSeen[k.ips[i]] && now() - lastSeen[k.ips[i]] < 300000) recentIp = true;     // per device, never per drawn node
    return C.deviceState({ shadowed: k.shadowed, lastQuery: k.lastQuery, serverNow: m.serverNow, recent: recentIp, offline: m.offline, paused: k.paused,
      unknown: !m.activityKnown || (privacyHides() && !k.lastQuery) });
  }
  var WORST = ['overridden', 'unreachable', 'offline', 'paused', 'unknown', 'quiet', 'active'];
  function worst(states) {
    var best = 'active', i;
    for (i = 0; i < states.length; i++) if (WORST.indexOf(states[i]) < WORST.indexOf(best)) best = states[i];
    return best;
  }
  function slotState(s) {
    if (s.kind === 'rest' || s.kind === 'add' || s.kind === 'example') return null;
    return worst(s.kids.map(function (k) { return kidState[k.key]; }));
  }
  var STATE_TEXT = { active: 'lvActive', unknown: 'lvUnknown', quiet: 'lvQuiet', paused: 'lvPaused', offline: 'lvOffline', overridden: 'lvOverridden', unreachable: 'lvUnreachable' };

  function renderSlots() {
    var host = el.devices, key = '', i, s, b;
    ipSlot = {}; kidByIp = {};
    slots = buildSlots(false);
    buildSlots(true).forEach(function (sl) { sl.kids.forEach(function (k) { k.ips.forEach(function (ip) { ipSlot[ip] = sl.id; kidByIp[ip] = k; }); }); });
    m.kids.forEach(function (k) { kidState[k.key] = stateOf(k); });
    for (i = 0; i < slots.length; i++) {
      s = slots[i];
      key += s.id + ':' + s.kind + ':' + s.kids.map(function (k) { return k.key; }).join(',') + '|';
    }
    key += (m.dir || '') + '|' + clientsHidden;
    var rebuild = key !== slotKey;
    host.setAttribute('data-count', String(slots.length));
    if (rebuild) {
      slotKey = key;
      host.textContent = '';
      for (i = 0; i < slots.length; i++) host.appendChild(buildNode(slots[i]));
    }
    for (i = 0; i < slots.length; i++) paintNode(slots[i], host.children[i]);
    if (rebuild && stage) stage.layout();
  }

  function buildNode(s) {
    var b = node('button', 'node node-dev node-' + s.kind);
    b.type = 'button';
    b.setAttribute('data-node', s.id);
    b.setAttribute('data-act', s.kind === 'add' ? 'openAdd' : 'node');
    b.setAttribute('data-which', s.id);
    var ico = node('span', 'node-ico');
    if (s.kind === 'more') ico.appendChild(node('span', 'node-more'));
    else ico.appendChild(icon(s.kind === 'rest' ? 'i-home' : s.kind === 'add' ? 'i-plus' : 'i-device'));
    b.appendChild(ico);
    b.appendChild(node('span', 'node-name'));
    var st = node('span', 'node-state');
    st.appendChild(node('i', 'sdot'));
    st.appendChild(node('span', 'node-state-text'));
    b.appendChild(st);
    return b;
  }

  function paintNode(s, b) {
    var name, state, st = slotState(s), cls = 'node node-dev node-' + s.kind;
    if (s.kind === 'kid') { name = kidName(s.kids[0]); state = t(STATE_TEXT[st]); cls += ' st-' + st; }
    else if (s.kind === 'more') { name = t('lvMore', { n: s.kids.length }); state = t(STATE_TEXT[st]); cls += ' st-' + st; b.querySelector('.node-more').textContent = '+' + s.kids.length; }
    else if (s.kind === 'add') { name = t('lvAddDevice'); state = t('lvAddHint'); cls += ' st-none'; }
    else if (s.kind === 'example') { name = t('lvExamplePhone'); state = t('lvExample'); cls += ' st-none'; }
    else { name = clientsHidden ? t('lvAllHome') : t('lvRest'); state = clientsHidden ? t('lvAllHomeSt') : t('lvRestSt'); cls += ' st-rest'; }
    b.className = cls + (b.classList.contains('is-focus') ? ' is-focus' : '');
    b.querySelector('.node-name').textContent = name;
    b.querySelector('.node-state-text').textContent = state;
    b.setAttribute('aria-label', name + '. ' + state);
    b.setAttribute('dir', 'auto');
  }

  /**
   * The board's traces. Every block has one way in and one way out, and every trace ends in an arrow at the pin it feeds:
   *   a device's OUT (bottom) -> the ask bus -> the box's IN (top)                           w-dev-N, w-rest
   *   the box's OUT (its inline-start side) -> the answer bus round the edge -> a device's IN (top)   a-dev-N, a-rest
   *   the box -> the internet's IN (the box looks the address up)                              w-out
   *   the internet's OUT -> the box (the reply)                                                w-in
   *   the box -> the black hole (a no goes nowhere)                                            w-hole
   * The internet never answers a device: the box does. Routes are measured from the drawn blocks, so they follow the layout.
   */
  var TRACE_GAP = 13;          // the answer bus: this far above the device row, and the rail this far from the board's edge
  function askRoute(id) {
    return function (S) {
      var d = S.box(id), b = S.box('box'), a, z, mid;
      if (!d || !b) return null;
      a = { x: d.cx, y: d.y + d.h }; z = { x: b.cx, y: b.y }; mid = Math.round((a.y + z.y) / 2);
      return [a, { x: a.x, y: mid }, { x: z.x, y: mid }, z];
    };
  }
  function answerRoute(id) {
    return function (S) {
      var d = S.box(id), b = S.box('box'), start, rail, top;
      if (!d || !b) return null;
      start = S.rtl ? { x: b.x + b.w, y: b.cy } : { x: b.x, y: b.cy };
      rail = S.rtl ? S.w - TRACE_GAP : TRACE_GAP;
      top = Math.max(4, d.y - TRACE_GAP);
      return [start, { x: rail, y: start.y }, { x: rail, y: top }, { x: d.cx, y: top }, { x: d.cx, y: d.y }];
    };
  }
  function lookupRoute(f, toNet) {              // two straight, parallel traces between the box's foot and the internet
    return function (S) {
      var b = S.box('box'), n = S.box('net'), x;
      if (!b || !n) return null;
      x = Math.round(b.x + b.w * (S.rtl ? 1 - f : f));
      x = Math.max(n.x + 14, Math.min(n.x + n.w - 14, x));
      return toNet ? [{ x: x, y: b.y + b.h }, { x: x, y: n.y }] : [{ x: x, y: n.y }, { x: x, y: b.y + b.h }];
    };
  }
  function holeRoute(S) {
    var b = S.box('box'), h = S.box('hole');
    if (!b || !h) return null;
    return S.rtl ? [{ x: b.x, y: b.cy }, { x: h.x + h.w, y: h.cy }] : [{ x: b.x + b.w, y: b.cy }, { x: h.x, y: h.cy }];
  }
  function wireDefs() {
    var defs = [], ids = [], i;
    for (i = 0; i < SLOT_MAX; i++) ids.push('dev-' + i);
    ids.push('rest');
    ids.forEach(function (id) { defs.push({ id: answerOf(id), from: 'box', to: id, route: answerRoute(id), pads: true }); });
    ids.forEach(function (id) { defs.push({ id: wireOf(id), from: id, to: 'box', route: askRoute(id), pads: true }); });
    defs.push({ id: 'w-out', from: 'box', to: 'net', route: lookupRoute(0.38, true), pads: true });
    defs.push({ id: 'w-in', from: 'net', to: 'box', route: lookupRoute(0.62, false), pads: true });
    defs.push({ id: 'w-hole', from: 'box', to: 'hole', route: holeRoute, pads: true, chamfer: 0 });
    return defs;
  }
  function wireOf(slotId) { return slotId === 'rest' ? 'w-rest' : 'w-' + slotId; }        // device -> box
  function answerOf(slotId) { return slotId === 'rest' ? 'a-rest' : 'a-' + slotId; }    // box -> device

  function paintWires() {
    if (!stage) return;
    var n = now();
    slots.forEach(function (s) {
      var cls = '', st = slotState(s), id = wireOf(s.id);
      if (s.kind === 'add' || s.kind === 'example') cls = 'is-quiet';
      else if (s.kind === 'rest') cls = 'is-rest';
      else if (st === 'overridden' || st === 'unreachable') cls = 'is-warn';
      else if (st === 'paused' || st === 'offline') cls = 'is-off';
      else cls = st === 'quiet' ? 'is-quiet' : 'is-ok';
      stage.wireState(id, cls + (wireUntil[id] > n ? ' is-active' : ''));
      stage.wireState(answerOf(s.id), cls + ' is-answer' + (wireUntil[answerOf(s.id)] > n ? ' is-active' : ''));
    });
    stage.wireState('w-out', wireUntil['w-out'] > n ? 'is-active' : '');
    stage.wireState('w-in', wireUntil['w-in'] > n ? 'is-active' : '');
    stage.wireState('w-hole', 'is-hole' + (wireUntil['w-hole'] > n ? ' is-active' : ''));
    var soonest = 0, k;
    for (k in wireUntil) if (wireUntil[k] > n && (!soonest || wireUntil[k] < soonest)) soonest = wireUntil[k];
    root.clearTimeout(wireTimer);
    if (soonest) wireTimer = root.setTimeout(paintWires, Math.max(100, soonest - n + 50));
  }
  function busy(id, ms) { wireUntil[id] = now() + ms; }

  // ------------------------------------------------------------------ the verdict (headline)
  function silentFor(k) { return k.lastQuery ? Math.max(0, m.serverNow - k.lastQuery) : 0; }    // seconds since it last asked the box
  function verdict() {
    var kids = m.kids, bad = [], over = 0, i, st, apps = m.blockedApps.length, n = kids.length, long = false;
    // Pi-hole's blocking switched off beats everything: no rule is applied, whatever the rest of the card knows.
    if (blockingOff) return { tone: 'warn', title: t('lvBlockOff'), sub: t('lvBlockOffSub') };
    if (!n) return { tone: 'none', title: t('lvNoKids'), sub: t('lvNoKidsSub') };
    for (i = 0; i < n; i++) {
      st = kidState[kids[i].key];
      if (st === 'overridden') over++;
      if (st === 'overridden' || st === 'unreachable') bad.push({ k: kids[i], st: st });
      if (st === 'quiet' && silentFor(kids[i]) > 7200) long = true;
    }
    if (m.timer && m.timerMode === 'free' && !over) return { tone: 'good', title: t('lvFree'), sub: t('lvFreeSub') };    // an overridden device is never "fine"
    if (bad.length) {
      if (bad.length === 1) return { tone: 'warn', title: t(bad[0].st === 'overridden' ? 'lvCheckOver' : 'lvCheckAway', { n: kidName(bad[0].k) }),
        sub: t(bad[0].st === 'overridden' ? 'lvCheckOverSub' : 'lvCheckAwaySub') };
      return { tone: 'warn', title: t('lvCheckMany', { n: bad.length }), sub: t('lvCheckManySub') };
    }
    if (m.offline) return { tone: 'off', title: t('lvOff'), sub: t('lvOffSub') };
    var sub = apps ? t('lvBlockedApps', { n: apps }) : t('lvNoBlockedApps');
    // "Working" only when a device was seen lately. Nobody online, the box cannot tell, or a device quiet for hours: the rules are set,
    // which is all that is known (a phone that left home Wi-Fi looks exactly like that).
    if (long || kids.every(function (k) { return kidState[k.key] === 'quiet' || kidState[k.key] === 'unknown'; })) return {
      tone: 'good', title: n === 1 ? t('lvSetOne', { n: kidName(kids[0]) }) : n === 2 ? t('lvSetTwo') : t('lvSetAll', { n: n }), sub: sub };
    if (kids.every(function (k) { return k.paused; })) return { tone: 'off', title: n === 1 ? t('lvPausedOne', { n: kidName(kids[0]) }) : t('lvPausedAll'), sub: t('lvPausedSub') };
    return { tone: 'good', title: n === 1 ? t('lvWorkingOne', { n: kidName(kids[0]) }) : n === 2 ? t('lvWorkingTwo') : t('lvWorkingAll', { n: n }), sub: sub };
  }
  function renderHead() {
    var v = verdict();
    el.title.textContent = v.title;
    el.sub.textContent = v.sub;
    el.icon.className = 'live-icon tone-' + v.tone;
    el.icon.firstChild.firstChild.setAttribute('href', '#' + (v.tone === 'warn' ? 'i-alert' : v.tone === 'off' ? 'i-moon-sm' : v.tone === 'none' ? 'i-info' : 'i-shield'));
    var pill = 'live', text = t('lvLive');
    if (queriesHidden) { pill = 'hidden'; text = t('lvTotalsOnly'); }
    else if (tour.on) { pill = 'example'; text = t('lvExample'); }
    else if (stale()) { pill = 'stale'; text = t('lvStale'); }
    else if (!active()) { pill = 'paused'; text = t('lvPausedPill'); }
    el.pill.className = 'live-pill is-' + pill;
    el.pillText.textContent = text;
    el.pill.hidden = collapsed;                              // a folded card has nothing to be "live" about
    if ((pill === 'stale') !== lastStale) { lastStale = pill === 'stale'; ariaSummary(); }    // a stall is announced, not only drawn
  }

  // ------------------------------------------------------------------ the box's voice
  function idleVoice() {
    if (blockingOff) return t('lvBoxBlockOff');
    if (m.offline) return t('lvBoxOff');
    if (m.blockedApps.length) return t('lvBoxBlocking', { n: m.blockedApps.length });
    return t('lvBoxOpen');
  }
  function say(text, tone, force) {
    var n = now();
    if (!force && n - voice.last < 1200) return;
    voice.last = n; voice.until = n + 2600;
    el.boxSub.textContent = text;
    el.box.setAttribute('data-tone', tone || '');
    root.clearTimeout(voice.timer);
    voice.timer = root.setTimeout(function () { el.boxSub.textContent = idleVoice(); el.box.setAttribute('data-tone', ''); }, 2600);
  }

  // ------------------------------------------------------------------ the numbers
  function setStat(id, key, to, format, still) {
    var n = el[id];
    var from = shown[key];
    shown[key] = to;
    L.tickNumber(n, from, to, still ? 0 : 700, format);          // the share is shown exactly, never counted up through whole numbers
  }
  function renderStats() {
    var total, blocked, share;
    if (!summary) {
      el.statChecked.textContent = '–'; el.statStopped.textContent = '–'; el.statShare.textContent = '–';
      return;
    }
    total = summary.total + delta.total;
    blocked = summary.blocked + delta.blocked;
    share = C.percent(blocked, total);
    setStat('statChecked', 'total', total, fmtTile);
    setStat('statStopped', 'blocked', blocked, fmtTile);
    setStat('statShare', 'share', share, function (n) { return n.toLocaleString(env.locale) + '%'; }, true);
  }
  function renderStaticText() {
    el.statCheckedLabel.textContent = t('lvChecked');
    el.statStoppedLabel.textContent = t('lvStopped');
    el.statShareLabel.textContent = t('lvShare');
    el.scope.textContent = t('lvScope');
    el.note.textContent = queriesHidden ? t('lvPrivacy') : t('lvNote');
    el.recentTitle.textContent = t('lvRecentTitle');
    el.tourBtn.textContent = t('lvTour');
    el.detailBtn.textContent = t('lvDetail');
    el.boxName.textContent = t('lvBox');
    el.netName.textContent = t('lvNet');
    el.nodeNet.setAttribute('aria-label', t('lvNet'));
    el.liveToggleText.textContent = t(collapsed ? 'lvShow' : 'lvHide');
    el.tourBack.textContent = t('lvBack');
    el.tourEnd.textContent = t('lvEndTour');
  }

  // ------------------------------------------------------------------ caption (what the picture means, or what was tapped)
  function defaultCaption() { return queriesHidden ? t('lvCaptionHidden') : t('lvCaption'); }
  function setText(node, text) { if (node.textContent !== text) node.textContent = text; }      // never rewrite the same words: live regions would repeat them
  function setCaption(text, hold, say) {
    setText(el.caption, text);
    if (say) { sayFlip = !sayFlip; el.say.textContent = text + (sayFlip ? '' : '\u00a0'); }      // announced for screen readers, even when tapped twice
    captionHeld = !!hold;
    root.clearTimeout(captionTimer);
    if (hold) captionTimer = root.setTimeout(function () { captionHeld = false; setText(el.caption, tour.on ? '' : defaultCaption()); }, hold);
  }
  function explain(which) {
    var text;
    if (which === 'box') text = t('lvExBox');
    else if (which === 'net') text = t('lvExNet');
    else if (which === 'rest') text = clientsHidden ? t('lvExAllHome') : t('lvExRest');
    else {
      var s = slots.filter(function (x) { return x.id === which; })[0];
      if (!s) return;
      if (s.kind === 'example') text = t('lvExExample');
      else if (s.kind === 'add') text = t('lvExAdd');
      else if (s.kind === 'more') { text = t('lvExMore', { n: s.kids.length }); if (env.goDevices) env.goDevices(); }
      else text = t('lvEx_' + slotState(s), { n: kidName(s.kids[0]) });
    }
    setCaption(text, 14000, true);
  }
  function explainStat(which) {
    if (collapsed) setCollapsed(false, true);                  // the words appear under the picture: open it so a tap is never silent
    setCaption(t(which === 'checked' ? 'lvExChecked' : which === 'stopped' ? 'lvExStopped' : 'lvExShare'), 16000, true);
  }

  // ------------------------------------------------------------------ recent events (text twin of the animation)
  function pushRecent(ev, parts, who, at) {
    var kind = ev.kind === 'blocked' ? 'stop' : 'go';
    var key = kind + '|' + (parts.app || parts.key) + '|' + who, top = recent[0];
    if (top && top.key === key) { top.n++; top.at = Math.max(top.at, at); }
    else { recent.unshift({ key: key, kind: kind, parts: parts, who: who, at: at, n: 1 }); if (recent.length > 5) recent.pop(); }
  }
  function ago(ms) {
    var s = Math.max(0, Math.round((now() - ms) / 1000));
    if (s < 10) return t('lvNow');
    if (s < 60) return t('lvSecs', { n: Math.floor(s / 10) * 10 });                 // in steps of 10, so the list is not rebuilt every tick
    return t('lvMins', { n: Math.round(s / 60) });
  }
  function renderRecent() {
    var ul = el.recentList, i, r, li, line, rows = [], lab, text, sig;
    if (queriesHidden || domainsHidden) rows.push({ empty: t(queriesHidden ? 'lvPrivacyShort' : 'lvPrivacyNames') });
    else if (!recent.length) rows.push({ empty: t('lvRecentEmpty') });
    else for (i = 0; i < recent.length; i++) {
      r = recent[i]; lab = resolveLabel(r.parts);
      // a reason ("Internet off") reads as it is; a name reads "Stopped YouTube"
      text = (r.kind === 'go' ? t('lvRecGo', { a: lab.text }) : (r.parts.key === 'lvWhyOff' || r.parts.key === 'lvWhyPaused') ? lab.text : t('lvRecStop', { a: lab.text })) +
        (r.who ? ' · ' + r.who : '') + (r.n > 1 ? ' ×' + r.n : '');
      rows.push({ kind: r.kind, text: text, when: ago(r.at) });
    }
    sig = JSON.stringify(rows);
    if (sig === lastRecentSig) return;                        // unchanged: leave the nodes alone (a reader's place is not reset)
    lastRecentSig = sig;
    ul.textContent = '';
    rows.forEach(function (row) {
      if (row.empty) { ul.appendChild(node('li', 'recent-empty', row.empty)); return; }
      li = node('li', 'recent-item is-' + row.kind);
      li.appendChild(icon(row.kind === 'stop' ? 'i-stop' : 'i-play'));
      line = node('span', 'recent-text', row.text);
      line.setAttribute('dir', 'auto');
      li.appendChild(line);
      li.appendChild(node('span', 'recent-when', row.when));
      ul.appendChild(li);
    });
  }
  function scheduleRepaint() {                // device states ("online now") follow the real activity, a few times a minute at most
    if (repaintTimer || !m) return;
    repaintTimer = root.setTimeout(function () { repaintTimer = 0; if (!started || !m) return; renderSlots(); renderHead(); paintWires(); }, 4000);
  }
  function scheduleRecent() {
    if (recentTimer) return;
    recentTimer = root.setTimeout(function () { recentTimer = 0; renderRecent(); }, 1500);
  }

  // ------------------------------------------------------------------ screen readers: one calm sentence, not one per packet
  function ariaSummary() {
    var cut = now() - 60000, c = 0, b = 0, i;
    minute = minute.filter(function (x) { return x.at >= cut; });
    for (i = 0; i < minute.length; i++) { c++; if (minute[i].blocked) b++; }
    var text = queriesHidden ? '' : stale() ? t('lvStale') : t('lvAria', { c: fmt(c), b: fmt(b) });
    if (text !== lastAria) { lastAria = text; el.aria.textContent = text; }
  }

  // ------------------------------------------------------------------ packets
  function slotFor(ev) {
    if (ev.hiddenClient) return 'rest';
    return ipSlot[ev.ip] || 'rest';
  }
  function whoFor(ev) {
    var k = ev.ip && !ev.hiddenClient ? kidByIp[ev.ip] : null;
    return k ? kidName(k) : '';
  }

  function animate(ev) {
    var slotId = ev.slot, w = wireOf(slotId), back = answerOf(slotId), lab = labelFor(ev), who = whoFor(ev), route, tone;
    if (!stage.wires[w] || !stage.wires[w].geom) return;
    var answer = stage.wires[back] && stage.wires[back].geom ? { wire: back, ms: 900 } : { wire: w, back: true, ms: 560 };
    var spoken = (ev.kind === 'blocked' && who ? who + ' · ' : '') + lab.text;      // a device is named for a stop only, never for an allowed lookup
    busy(w, 5000);
    if (ev.kind === 'blocked') {
      tone = 'is-blocked';
      busy('w-hole', 5000);
      route = [{ wire: w, ms: 650 }, { hold: 180 }, { wire: 'w-hole', ms: 420 }, { sink: 650 }];
    } else if (ev.kind === 'memory') {
      tone = 'is-memory';                              // the box knew the answer already: straight back, nothing goes out
      busy(back, 5000);
      route = [{ wire: w, ms: 650 }, { hold: 160 }, answer];
    } else {
      tone = 'is-allowed';
      busy('w-out', 5000); busy('w-in', 5000); busy(back, 5000);
      route = [{ wire: w, ms: 600 }, { hold: 140 }, { wire: 'w-out', ms: 460 }, { hold: 160 }, { wire: 'w-in', ms: 460 }, { hold: 120 }, answer];
    }
    paintWires();
    stage.send({
      // It travels as a question; the box decides what it becomes (a ghost for a no) when it gets there.
      label: lab.text, badge: lab.badge, color: lab.color, tone: 'is-asking ' + tone, route: route,
      onStep: function (i, api) {
        if (i === 0) {                                   // arrived at the box
          api.tone(tone);
          stage.pulse('box', ev.kind === 'blocked' ? 'is-refusing' : 'is-checking', 650);
          say(t(ev.kind === 'blocked' ? 'lvSayNo' : 'lvSayYes', { a: spoken }), ev.kind === 'blocked' ? 'no' : 'yes');
        }
        if (ev.kind === 'blocked' && i === 2) { api.tone(tone + ' is-sinking'); stage.pulse('hole', 'is-eating', 700); }
        if (ev.kind === 'allowed' && i === 1) api.label(t('lvLookup'));            // the box looks the address up outside: only that goes on
        if (ev.kind === 'allowed' && i === 2) stage.pulse('net', 'is-reached', 600);
        if (ev.kind === 'allowed' && i === 4) { api.tone(tone + ' is-answer'); api.label(lab.text); }   // the reply is back at the box
        if (i === route.length - 1 && ev.kind !== 'blocked') stage.pulse(slotId, 'is-answered', 600);   // the answer reached the device's IN
      }
    });
  }

  function setCollapsed(v, remember) {
    collapsed = !!v;
    el.live.classList.toggle('is-collapsed', collapsed);
    el.liveToggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    el.liveToggleText.textContent = t(collapsed ? 'lvShow' : 'lvHide');
    if (remember) store(collapsed ? 'closed' : 'open');
    if (collapsed) { if (tour.on) endTour(); stage.clear(); pacer.queue.length = 0; fails.queries = 0; }
    else { feed = new C.Feed({ length: 60 }); stage.layout(); wake(); }          // start again from "now": nothing old is replayed
    el.live.classList.toggle('is-paused', !active());
    if (m) renderHead();
  }

  /** Hands out queued events at a gentle pace. */
  function pump() {
    pumpTimer = 0;
    if (!active() || tour.on) { pacer.queue.length = 0; return; }
    var ev = pacer.next(now());
    if (ev) animate(ev);
    if (pacer.queue.length) pumpTimer = root.setTimeout(pump, Math.max(60, pacer.gap - (now() - pacer.last) + 20));
  }
  function schedulePump() {
    if (pumpTimer || !pacer.queue.length) return;
    pumpTimer = root.setTimeout(pump, Math.max(0, pacer.gap - (now() - pacer.last)));
  }

  // ------------------------------------------------------------------ data
  function pollSummary() {
    return withTimeout(Promise.all([
      env.call('GET', '/api/stats/summary'),
      env.call('GET', '/api/dns/blocking').catch(function () { return null; })       // missing or odd: never breaks the picture
    ]), 10000).then(function (r) {
      var j = r[0], q = (j && j.queries) || {}, b = r[1] && r[1].blocking;
      summary = { total: q.total || 0, blocked: q.blocked || 0, cached: q.cached || 0, forwarded: q.forwarded || 0, unique: q.unique_domains || 0,
        listSize: ((j.gravity || {}).domains_being_blocked) || 0, listAt: ((j.gravity || {}).last_update) || 0, clients: ((j.clients || {}).active) || 0 };
      summaryAt = env.serverNow ? env.serverNow() : 0;         // queries after this moment are not in the summary yet: only those are added
      delta = { total: 0, blocked: 0 };
      blockingOff = b === 'disabled' || b === 'failure';
      fails.summary = 0;
      renderStats(); renderHead();
      if (m && !tour.on) setText(el.boxSub, voice.until <= now() ? idleVoice() : el.boxSub.textContent);
      if (detailOpen) renderDetailNumbers();
    }, function (e) { fails.summary++; renderHead(); throw e; });
  }

  function serverAge(ev) {                                  // seconds since the event, by the BOX's clock
    var n = env.serverNow ? env.serverNow() : 0;
    return n && ev.time ? Math.max(0, n - ev.time) : 0;
  }
  function pollQueries() {
    if (collapsed) return Promise.resolve();                 // the picture is closed: only the totals are kept up to date
    return withTimeout(env.call('GET', feed.url()), 10000).then(function (j) {
      var evs = feed.ingest(j, env.serverNow ? env.serverNow() : 0), i, ev, wasQ = queriesHidden, ts, parts;
      fails.queries = 0;
      queriesHidden = feed.privacy;
      if (queriesHidden !== wasQ) { renderStaticText(); setCaption(defaultCaption()); renderRecent(); renderHead(); }
      if (evs.length) {                                          // privacy is judged by the whole batch: one odd row never switches names off
        var allC = evs.every(function (e) { return e.hiddenClient; }), allD = evs.every(function (e) { return e.hiddenDomain; });
        if (allC !== clientsHidden) { clientsHidden = allC; renderSlots(); }
        if (allD !== domainsHidden) { domainsHidden = allD; renderRecent(); }
      }
      var fresh = [];
      for (i = 0; i < evs.length; i++) {
        ev = evs[i];
        ev.app = ev.hiddenDomain ? null : C.appFor(ev.domain, env.domains);
        ev.slot = slotFor(ev);
        ev.child = ev.slot !== 'rest';
        var late = serverAge(ev);                                              // after a gap (tab was hidden) old events are listed, not replayed
        ts = now() - late * 1000;
        if (ev.ip && !ev.hiddenClient && (!lastSeen[ev.ip] || ts > lastSeen[ev.ip])) lastSeen[ev.ip] = ts;       // per device, on the event's own time
        if (!summaryAt || ev.time > summaryAt) { delta.total++; if (ev.kind === 'blocked') delta.blocked++; }   // the summary already holds the older ones
        minute.push({ at: ts, blocked: ev.kind === 'blocked' });
        parts = labelParts(ev);
        if (ev.kind === 'blocked' || parts.app) pushRecent(ev, parts, ev.kind === 'blocked' ? whoFor(ev) : '', ts);
        if (late <= 20) fresh.push(ev);
      }
      if (fresh.length) {
        if (!tour.on && active()) { pacer.push(fresh); schedulePump(); }
        renderStats(); scheduleRecent(); scheduleRepaint();
      }
    }, function (e) { fails.queries++; renderHead(); throw e; });
  }

  function startPolling() {
    if (poller) return;
    poller = new C.Poller({
      tasks: [
        { name: 'summary', every: 15000, run: pollSummary },
        { name: 'queries', every: 3500, run: pollQueries }
      ],
      visible: watching
    });
    poller.start();
  }

  // ------------------------------------------------------------------ tour: examples that never touch a number
  var TOUR = ['lvTour1', 'lvTour2', 'lvTour3', 'lvTour4', 'lvTour5'];
  function focusNodes(ids) {
    Array.prototype.forEach.call(el.stage.querySelectorAll('.node'), function (n) {
      n.classList.toggle('is-focus', ids.indexOf(n.getAttribute('data-node')) >= 0);
    });
  }
  function example(kind) {
    var w = 'w-dev-0', back = 'a-dev-0', slotId = 'dev-0', route, tone, label = t('lvExampleApp');
    if (!stage.wires[w] || !stage.wires[w].geom) { w = 'w-rest'; back = 'a-rest'; slotId = 'rest'; }
    var answer = stage.wires[back] && stage.wires[back].geom ? { wire: back, ms: 1100 } : { wire: w, back: true, ms: 650 };
    busy(w, 5000);
    if (kind === 'blocked') { tone = 'is-blocked is-example'; busy('w-hole', 5000);
      route = [{ wire: w, ms: 800 }, { hold: 220 }, { wire: 'w-hole', ms: 500 }, { sink: 800 }]; }
    else if (kind === 'allowed') { tone = 'is-allowed is-example'; busy('w-out', 5000); busy('w-in', 5000); busy(back, 5000);
      route = [{ wire: w, ms: 750 }, { hold: 200 }, { wire: 'w-out', ms: 600 }, { hold: 200 }, { wire: 'w-in', ms: 600 }, { hold: 150 }, answer]; }
    else { tone = 'is-example'; route = [{ wire: w, ms: 800 }, { hold: 700 }]; }       // on its way to the box, and checked there
    paintWires();
    stage.send({ label: label, tone: 'is-asking ' + tone, route: route, onStep: function (i, api) {
      if (i === 0) { api.tone(tone); stage.pulse('box', kind === 'blocked' ? 'is-refusing' : 'is-checking', 700); say(t(kind === 'blocked' ? 'lvSayNoEx' : 'lvSayYesEx'), kind === 'blocked' ? 'no' : 'yes', true); }
      if (kind === 'blocked' && i === 2) { api.tone(tone + ' is-sinking'); stage.pulse('hole', 'is-eating', 800); }
      if (kind === 'allowed' && i === 1) api.label(t('lvLookup'));
      if (kind === 'allowed' && i === 2) stage.pulse('net', 'is-reached', 600);
      if (kind === 'allowed' && i === 4) { api.tone(tone + ' is-answer'); api.label(label); }
      if (kind === 'allowed' && i === route.length - 1) stage.pulse(slotId, 'is-answered', 700);
    } });
  }
  function tourTexts() {                                      // also called when the language changes mid-tour
    setText(el.tourStep, t(TOUR[tour.i]));
    setText(el.tourCount, t('lvStep', { i: tour.i + 1, n: TOUR.length }));
    setText(el.tourNext, t(tour.i === TOUR.length - 1 ? 'lvDone' : 'lvNext'));
    setText(el.tourBack, t('lvBack'));
    setText(el.tourEnd, t('lvEndTour'));
    if (tour.i === 0 && doc.activeElement === el.tourBack) el.tourNext.focus({ preventScroll: true });   // Back disappears: focus moves, never falls to the page
    el.tourBack.style.visibility = tour.i === 0 ? 'hidden' : '';        // keeps its place, so a double tap on Next never lands on Back
    el.tourBack.disabled = tour.i === 0;
  }
  function tourStep(i) {
    root.clearTimeout(tour.timer);
    tour.i = Math.max(0, Math.min(TOUR.length - 1, i));
    stage.clear();
    tourTexts();
    var kinds = [null, 'plain', 'allowed', 'blocked', null];
    var foci = [['dev-0', 'rest'], ['dev-0', 'box'], ['dev-0', 'box', 'net'], ['dev-0', 'box', 'hole'], ['box']];
    focusNodes(foci[tour.i]);
    var run = function () { if (kinds[tour.i] && tour.on && active()) example(kinds[tour.i]); };
    run();
    if (kinds[tour.i]) tour.timer = root.setTimeout(function again() { run(); tour.timer = root.setTimeout(again, 5200); }, 5200);
  }
  function startTour() {
    if (tour.on) return;
    if (collapsed) setCollapsed(false, true);
    tour.on = true;
    pacer.queue.length = 0;
    el.live.classList.add('is-touring');
    el.tour.hidden = false;
    el.caption.textContent = '';
    renderSlots(); renderHead();
    tourStep(0);
    if (el.live.scrollIntoView) el.live.scrollIntoView({ behavior: L.reducedMotion() ? 'auto' : 'smooth', block: 'start' });   // picture and words in one screen
    var first = el.tour.querySelector('#tourNext'); if (first) first.focus({ preventScroll: true });
  }
  function endTour() {
    if (!tour.on) return;
    tour.on = false;
    root.clearTimeout(tour.timer);
    stage.clear();
    focusNodes([]);
    el.live.classList.remove('is-touring');
    el.tour.hidden = true;
    setCaption(defaultCaption());
    el.boxSub.textContent = idleVoice(); el.box.setAttribute('data-tone', '');
    renderSlots(); renderHead(); paintWires();
    var b = el.tourBtn; if (b) b.focus();
  }

  // ------------------------------------------------------------------ detail sheet
  function fmtAgoLong(epoch) {
    if (!epoch) return '';
    var diff = Math.round((epoch - m.serverNow) / 60);
    try {
      var rtf = new Intl.RelativeTimeFormat(env.locale, { numeric: 'auto' });
      if (diff > -60) return rtf.format(diff, 'minute');
      if (diff > -1440) return rtf.format(Math.round(diff / 60), 'hour');
      return rtf.format(Math.round(diff / 1440), 'day');
    } catch (e) { return ''; }
  }
  function section(title) {
    var s = node('section', 'sheet-sec');
    s.appendChild(node('h3', '', title));
    return s;
  }
  function openDetail() {
    var body = el.sheetBody, sec, ol, i, ul;
    detailOpen = true;
    el.sheetTitle.textContent = t('lvDetailTitle');
    body.textContent = '';

    sec = section(t('lvHowTitle'));
    ol = node('ol', 'how');
    ['lvHow1', 'lvHow2', 'lvHow3', 'lvHow4'].forEach(function (k) {
      var li = node('li', 'how-item');
      li.appendChild(node('strong', '', t(k + 'T')));
      li.appendChild(node('span', '', t(k)));
      ol.appendChild(li);
    });
    sec.appendChild(ol);
    body.appendChild(sec);

    sec = section(t('lvNumbersTitle'));
    sec.appendChild(node('p', 'muted small', t('lvScope')));
    ul = node('ul', 'facts'); ul.id = 'detailFacts';
    sec.appendChild(ul);
    var hoursTitle = node('h4', '', t('lvHours'));
    sec.appendChild(hoursTitle);
    var bars = node('div', 'hours'); bars.id = 'detailHours'; bars.setAttribute('role', 'img'); bars.setAttribute('aria-label', t('lvHours'));
    sec.appendChild(bars);
    var axis = node('div', 'hours-axis');                    // what the bars mean: two colours, and which end is "now"
    axis.appendChild(node('span', '', t('lvEarlier')));
    var key = node('span', 'hours-key');
    key.appendChild(node('i', 'key key-total')); key.appendChild(node('span', '', t('lvChecked')));
    key.appendChild(node('i', 'key key-blocked')); key.appendChild(node('span', '', t('lvStopped')));
    axis.appendChild(key);
    axis.appendChild(node('span', '', t('lvNowShort')));
    sec.appendChild(axis);
    var topTitle = node('h4', '', t('lvTopTitle'));
    sec.appendChild(topTitle);
    var top = node('ul', 'toplist'); top.id = 'detailTop';
    sec.appendChild(top);
    body.appendChild(sec);

    sec = section(t('lvCannotTitle'));
    ul = node('ul', 'cannot');
    ['lvCannot1', 'lvCannot2', 'lvCannot3', 'lvCannot4'].forEach(function (k) { ul.appendChild(node('li', '', t(k))); });
    sec.appendChild(ul);
    body.appendChild(sec);

    sec = section(t('lvWordsTitle'));
    var dl = node('dl', 'words');
    ['lvWord1', 'lvWord2', 'lvWord3', 'lvWord4', 'lvWord5'].forEach(function (k) {
      dl.appendChild(node('dt', '', t(k + 'T')));
      dl.appendChild(node('dd', '', t(k)));
    });
    sec.appendChild(dl);
    body.appendChild(sec);

    renderDetailNumbers();
    detail = { loading: true };
    Promise.all([
      env.call('GET', '/api/history').catch(function () { return null; }),
      env.call('GET', '/api/stats/top_domains?blocked=true&count=60').catch(function () { return null; })
    ]).then(function (r) {
      if (!detailOpen) return;
      var topHidden = !!r[1] && (r[1].total_queries === -1 || domainsHidden || queriesHidden);     // FTL answers with -1 from privacy level 1 up
      detail = { hours: r[0] ? C.hourly(r[0]) : null,
        top: topHidden ? 'hidden' : r[1] ? C.topByApp(r[1].domains || [], env.domains, 5, m.blockedApps) : null };
      renderDetailNumbers();
    });
    env.openSheet();
  }
  function closeDetail() { detailOpen = false; detail = null; }

  function renderDetailNumbers() {
    var facts = $('detailFacts'), hours = $('detailHours'), top = $('detailTop');
    if (!facts) return;
    facts.textContent = '';
    function fact(label, value) {
      var li = node('li', 'fact');
      li.appendChild(node('span', 'fact-value', value));
      li.appendChild(node('span', 'fact-label', label));
      facts.appendChild(li);
    }
    if (summary) {
      var total = summary.total || 0;
      fact(t('lvFactMemory'), total ? C.percent(summary.cached, total) + '%' : '–');
      fact(t('lvFactPlaces'), fmt(summary.unique));
      fact(t('lvFactList'), fmt(summary.listSize));
      fact(t('lvFactDevices'), fmt(summary.clients));
    } else fact(t('lvLoading'), '–');
    if (summary && summary.listAt) {
      var ago2 = fmtAgoLong(summary.listAt);
      if (ago2) { var u = node('li', 'fact fact-wide'); u.appendChild(node('span', 'fact-label', t('lvFactListAt', { t: ago2 }))); facts.appendChild(u); }
    }
    hours.textContent = ''; top.textContent = '';
    if (!detail || detail.loading) { hours.appendChild(node('span', 'muted small', t('lvLoading'))); return; }
    var hs = detail.hours, max = 1, i, b, col;
    if (hs) {
      for (i = 0; i < hs.length; i++) max = Math.max(max, hs[i].total);
      for (i = 0; i < hs.length; i++) {
        col = node('span', 'hour');
        b = node('span', 'hour-total'); b.style.height = Math.round(100 * hs[i].total / max) + '%';
        var bb = node('span', 'hour-blocked'); bb.style.height = hs[i].total ? Math.round(100 * hs[i].blocked / hs[i].total) + '%' : '0%';
        b.appendChild(bb); col.appendChild(b); hours.appendChild(col);
      }
    } else hours.appendChild(node('span', 'muted small', t('lvUnavailable')));
    if (detail.top === 'hidden') top.appendChild(node('li', 'muted small', t('lvPrivacyNames')));
    else if (detail.top && detail.top.length) {
      var peak = 1;
      detail.top.forEach(function (x) { peak = Math.max(peak, x.count); });
      detail.top.forEach(function (r) {
        var a = r.app && appInfo(r.app), li = node('li', 'top-item');
        var badge = node('span', 'top-badge' + (a ? '' : ' is-other'), a ? a.mono : '·');
        if (a && a.color) badge.style.setProperty('--brand', a.color);
        li.appendChild(badge);
        var mid = node('span', 'top-mid');
        mid.appendChild(node('span', 'top-name', a ? a.name : t('lvOther')));
        var bar = node('span', 'top-bar'); var fill = node('span', 'top-fill'); fill.style.width = Math.max(4, Math.round(100 * r.count / peak)) + '%'; bar.appendChild(fill);
        mid.appendChild(bar);
        li.appendChild(mid);
        li.appendChild(node('span', 'top-count', fmt(r.count)));
        top.appendChild(li);
      });
    } else if (detail.top) top.appendChild(node('li', 'muted small', t('lvTopEmpty')));
    else top.appendChild(node('li', 'muted small', t('lvUnavailable')));
  }

  // ------------------------------------------------------------------ public
  function onClick(ev) {
    var n = ev.target.closest ? ev.target.closest('#live [data-act]') : null;
    if (!n) return false;
    var act = n.getAttribute('data-act');
    if (act === 'node') explain(n.getAttribute('data-which'));
    else if (act === 'stat') explainStat(n.getAttribute('data-which'));
    else if (act === 'liveToggle') setCollapsed(!collapsed, true);
    else if (act === 'tour') startTour();
    else if (act === 'tourNext') { if (tour.i >= TOUR.length - 1) endTour(); else tourStep(tour.i + 1); }
    else if (act === 'tourBack') tourStep(tour.i - 1);
    else if (act === 'tourEnd') endTour();
    else if (act === 'detail') openDetail();
    else return false;
    return true;
  }

  function cache() {
    ['live', 'liveIcon', 'liveTitle', 'liveSub', 'livePill', 'livePillText', 'stage', 'stageDevices', 'nodeBox', 'boxName', 'boxSub', 'nodeNet', 'netName',
      'liveCaption', 'liveTour', 'tourStep', 'tourCount', 'tourBack', 'tourNext', 'tourEnd', 'statChecked', 'statCheckedLabel', 'statStopped', 'statStoppedLabel',
      'statShare', 'statShareLabel', 'liveScope', 'liveNote', 'liveRecent', 'liveRecentTitle', 'liveRecentList', 'liveAria', 'tourBtn', 'detailBtn',
      'sheetTitle', 'sheetBody', 'liveSheet', 'liveToggle', 'liveToggleText', 'liveSay'].forEach(function (id) { el[id] = $(id); });
    el.devices = el.stageDevices; el.title = el.liveTitle; el.sub = el.liveSub; el.icon = el.liveIcon; el.pill = el.livePill; el.pillText = el.livePillText;
    el.box = el.nodeBox; el.caption = el.liveCaption; el.tour = el.liveTour; el.scope = el.liveScope; el.note = el.liveNote;
    el.recentTitle = el.liveRecentTitle; el.recentList = el.liveRecentList; el.aria = el.liveAria; el.say = el.liveSay;
  }

  function wake() { if (poller) poller.wake(); }
  function onGate() {                       // page hidden, a dialog opened or closed, the card scrolled away
    if (!started) return;
    if (!active()) { stage.clear(); pacer.queue.length = 0; if (tour.on) root.clearTimeout(tour.timer); }
    else { if (tour.on) tourStep(tour.i); }
    if (watching()) wake();
    el.live.classList.toggle('is-paused', !active());
    renderHead();
  }

  /**
   * init({ call, t, locale, domains, apps, openSheet, goDevices }): `call(method, path)` is the page's signed-in API call;
   * `t(key, vars)` its translator; `apps` maps an app id to { name, mono, color }; `domains` is pb/domains.json's "domains".
   */
  function init(options) {
    if (started) return;
    env = options;
    cache();
    if (!el.live || !el.stage) return;
    feed = new C.Feed({ length: 60 });
    pacer = new C.Pacer({ gap: 380, max: 10 });
    stage = new L.Stage(el.stage, { wires: wireDefs(), poolSize: 8, onLayout: function () { paintWires(); } });
    started = true;
    el.live.hidden = false;
    collapsed = store() === 'closed';
    if (collapsed) setCollapsed(true, false);
    if (!wired) {                              // the page may sign out and in again: listen only once
      wired = true;
      doc.addEventListener('click', function (ev) { if (started && el.live.contains(ev.target)) onClick(ev); });
      doc.addEventListener('visibilitychange', onGate);
      doc.addEventListener('close', function (ev) { if (ev.target && ev.target.id === 'liveSheet') closeDetail(); onGate(); }, true);
    }
    if (root.IntersectionObserver) {
      io = new root.IntersectionObserver(function (entries) { inView = entries[entries.length - 1].isIntersecting; onGate(); }, { threshold: 0.05 });
      io.observe(el.live);
    }
    ariaTimer = root.setInterval(function () { if (active()) ariaSummary(); }, 30000);
    recentTick = root.setInterval(function () { if (active() && el.liveRecent.open) renderRecent(); }, 10000);
    if (L.reducedMotion()) el.liveRecent.open = true;           // no movement: the list is the picture's text twin
  }

  /** The page hands over its view-model after every render (and on language change). Cheap: rebuilds nodes only when they change. */
  function update(model) {
    if (!started) return;
    var langChanged = !!m && m.lang !== model.lang;
    m = model;
    if (model.locale) env.locale = model.locale;
    if (langChanged) { captionHeld = false; root.clearTimeout(captionTimer); }       // a held explanation would stay in the old language
    renderSlots();
    renderStaticText();
    renderHead();
    if (!tour.on) { if (voice.until <= now()) setText(el.boxSub, idleVoice()); if (!captionHeld) setText(el.caption, defaultCaption()); }
    renderRecent();
    renderStats();
    paintWires();
    stage.layout();
    if (tour.on) tourTexts();
    startPolling();
  }
  function stop() {
    if (!started) return;
    if (poller) { poller.stop(); poller = null; }
    if (stage) stage.clear();
    root.clearTimeout(pumpTimer); pumpTimer = 0;
    if (tour.on) endTour();
    started = false;
    el.live.hidden = true;
    if (io) { io.disconnect(); io = null; }
    root.clearInterval(ariaTimer); root.clearInterval(recentTick);
    root.clearTimeout(repaintTimer); repaintTimer = 0; root.clearTimeout(recentTimer); recentTimer = 0;
    root.clearTimeout(wireTimer); wireTimer = 0; root.clearTimeout(voice.timer); root.clearTimeout(captionTimer); captionHeld = false;
    if (stage) { stage.destroy(); stage = null; }
    summary = null; recent = []; lastSeen = {}; slotKey = ''; wireUntil = {}; queriesHidden = false; clientsHidden = false; domainsHidden = false;
    shown = { total: 0, blocked: 0, share: 0 }; delta = { total: 0, blocked: 0 }; fails = { summary: 0, queries: 0 }; minute = []; lastAria = '';
    summaryAt = 0; blockingOff = false; lastStale = false; lastRecentSig = ''; kidByIp = {}; ipSlot = {}; m = null; el.say.textContent = ''; el.aria.textContent = '';
  }

  root.PBPicture = { init: init, update: update, stop: stop, wake: wake };
})(window);
