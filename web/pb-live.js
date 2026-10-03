/* pb-live.js: the live picture on the parent page.
 *
 * Stage: the generic drawing engine. It measures the diagram's nodes (elements with data-node="id"), draws the wires between
 * them as SVG curves, and sends packets along those wires. Packets are a small fixed pool of elements that are reused, one
 * requestAnimationFrame loop runs only while something moves, and with prefers-reduced-motion nothing moves at all: the
 * callbacks still run, so the numbers and states stay right.
 *
 * Everything shown is set with textContent / attributes (never innerHTML): domain names come from the network.
 * Depends on pb-core.js (PBCore). ES5 on purpose, like the rest of the page.
 */
(function (root) {
  'use strict';

  var C = root.PBCore;
  var SVGNS = 'http://www.w3.org/2000/svg';

  function svg(tag, attrs) {
    var n = document.createElementNS(SVGNS, tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    return n;
  }
  function div(cls, text) {
    var n = document.createElement('div');
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function reducedMotion() {
    return !!(root.matchMedia && root.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }

  // ====================================================================== Stage
  /**
   * new Stage(host, { wires: [{ id, from, to }], poolSize: 8, onLayout: fn })
   * host: position:relative element containing the nodes, an <svg class="live-wires"> and a <div class="live-packets">.
   */
  function Stage(host, options) {
    options = options || {};
    this.host = host;
    this.svg = host.querySelector('svg.live-wires');
    this.layer = host.querySelector('.live-packets');
    this.defs = options.wires || [];
    this.onLayout = options.onLayout || function () {};
    this.wires = {};                  // id -> { g, base, flow, geom }
    this.pool = [];
    this.active = [];
    this.frameId = 0;
    this.paused = false;
    this.reduced = reducedMotion();
    this.timers = [];
    var self = this, i;
    for (i = 0; i < this.defs.length; i++) this._buildWire(this.defs[i]);
    for (i = 0; i < (options.poolSize || 8); i++) this.pool.push(this._buildPacket());
    this._onResize = function () { self.layout(); };
    root.addEventListener('resize', this._onResize);
    if (root.ResizeObserver) { this.ro = new root.ResizeObserver(function () { self.layout(); }); this.ro.observe(host); }
    var mq = root.matchMedia && root.matchMedia('(prefers-reduced-motion: reduce)');
    if (mq && mq.addEventListener) mq.addEventListener('change', function () { self.reduced = reducedMotion(); if (self.reduced) self.clear(); });
  }

  Stage.prototype._buildWire = function (def) {
    var g = svg('g', { 'class': 'wire', 'data-wire': def.id });
    var base = svg('path', { 'class': 'wire-base', fill: 'none' });
    var flow = svg('path', { 'class': 'wire-flow', fill: 'none' });
    g.appendChild(base); g.appendChild(flow);
    this.svg.appendChild(g);
    this.wires[def.id] = { def: def, g: g, base: base, flow: flow, geom: null };
  };

  Stage.prototype._buildPacket = function () {
    var pk = div('pk');
    pk.hidden = true;
    var badge = div('pk-badge'), label = div('pk-label');
    pk.appendChild(badge); pk.appendChild(label);
    this.layer.appendChild(pk);
    return { el: pk, badge: badge, label: label, busy: false };
  };

  /** Where a node is, relative to the host: { x, y, w, h, cx, cy }, or null while it is not displayed. */
  Stage.prototype.box = function (id) {
    var n = this.host.querySelector('[data-node="' + id + '"]');
    if (!n || (!n.offsetWidth && !n.offsetHeight)) return null;
    var h = this.host.getBoundingClientRect(), r = n.getBoundingClientRect();
    return { x: r.left - h.left, y: r.top - h.top, w: r.width, h: r.height, cx: r.left - h.left + r.width / 2, cy: r.top - h.top + r.height / 2 };
  };

  /** The point on a node's edge that faces `toward`: the middle of the side the wire leaves from. */
  function port(b, toward) {
    var dx = toward.x - b.cx, dy = toward.y - b.cy;
    if (Math.abs(dx) * b.h > Math.abs(dy) * b.w) return { x: dx > 0 ? b.x + b.w : b.x, y: b.cy };
    return { x: b.cx, y: dy > 0 ? b.y + b.h : b.y };
  }

  /** (Re)draw every wire from the current positions of its two nodes. Call after layout changes. */
  Stage.prototype.layout = function () {
    var h = this.host.getBoundingClientRect(), id, w, a, b, pa, pb;
    this.svg.setAttribute('viewBox', '0 0 ' + Math.max(1, Math.round(h.width)) + ' ' + Math.max(1, Math.round(h.height)));
    for (id in this.wires) {
      w = this.wires[id];
      a = this.box(w.def.from); b = this.box(w.def.to);
      if (!a || !b) { w.g.setAttribute('display', 'none'); w.geom = null; continue; }
      pa = port(a, { x: b.cx, y: b.cy }); pb = port(b, { x: a.cx, y: a.cy });
      w.geom = C.wire(pa, pb);
      w.base.setAttribute('d', w.geom.d); w.flow.setAttribute('d', w.geom.d);
      w.g.removeAttribute('display');
    }
    this.onLayout(this);
  };

  /** Set a wire's look: any combination of classes such as 'is-active', 'is-blocked', 'is-quiet', 'is-warn'. */
  Stage.prototype.wireState = function (id, classes) {
    var w = this.wires[id];
    if (w) w.g.setAttribute('class', 'wire' + (classes ? ' ' + classes : ''));
  };

  /** Flash a node: adds `cls` for `ms` (CSS does the animation). */
  Stage.prototype.pulse = function (nodeId, cls, ms) {
    var n = this.host.querySelector('[data-node="' + nodeId + '"]');
    if (!n) return;
    n.classList.remove(cls);
    void n.offsetWidth;                                             // restart the animation if it is already running
    n.classList.add(cls);
    this._later(function () { n.classList.remove(cls); }, ms || 700);
  };

  Stage.prototype._later = function (fn, ms) {
    var self = this, t = root.setTimeout(function () {
      var i = self.timers.indexOf(t); if (i >= 0) self.timers.splice(i, 1);
      fn();
    }, ms);
    this.timers.push(t);
  };

  /**
   * Send a packet.
   *   spec = { label, color, tone, route: [ { wire, back, ms } | { hold: ms } ], onStep(i), onDone() }
   * The route is a list of legs; onStep(i) runs when leg i has finished (that is where the family box reacts, a block
   * happens, ...). `tone` is a CSS class on the packet ('is-allowed', 'is-blocked', 'is-memory', 'is-example') and can be
   * changed from onStep through the packet argument: packet.tone('is-blocked').
   * With reduced motion, a hidden page, or no free packet, nothing moves: every callback still runs, in order.
   * Returns true when it was animated.
   */
  Stage.prototype.send = function (spec) {
    var self = this, pk = null, i;
    var api = { tone: function () {}, label: function () {} };
    if (this.reduced || this.paused || document.hidden) return this._instant(spec, api);
    for (i = 0; i < this.pool.length; i++) if (!this.pool[i].busy) { pk = this.pool[i]; break; }
    if (!pk) return this._instant(spec, api);
    for (i = 0; i < spec.route.length; i++) {
      if (spec.route[i].wire && !(this.wires[spec.route[i].wire] && this.wires[spec.route[i].wire].geom)) return this._instant(spec, api);
    }
    pk.busy = true;
    pk.label.textContent = spec.label || '';
    pk.badge.textContent = spec.badge || '';
    pk.badge.hidden = !spec.badge;
    if (spec.color) pk.el.style.setProperty('--brand', spec.color); else pk.el.style.removeProperty('--brand');
    pk.el.className = 'pk' + (spec.tone ? ' ' + spec.tone : '');
    pk.el.hidden = false;
    pk.el.style.opacity = '0';
    var p = { pk: pk, spec: spec, i: 0, t0: null, w: pk.el.offsetWidth, h: pk.el.offsetHeight, fade: 0 };
    api.tone = function (cls) { pk.el.className = 'pk ' + cls; };
    api.label = function (text) { pk.label.textContent = text; };
    p.api = api;
    this.active.push(p);
    if (!this.frameId) this.frameId = root.requestAnimationFrame(function (t) { self._frame(t); });
    return true;
  };

  Stage.prototype._instant = function (spec, api) {
    var i;
    try {
      for (i = 0; i < spec.route.length; i++) if (spec.onStep) spec.onStep(i, api);
      if (spec.onDone) spec.onDone();
    } catch (e) { /* a broken callback must not stop the picture */ }
    return false;
  };

  Stage.prototype._frame = function (now) {
    var self = this, i, p, leg, w, e, pt, keep = [];
    this.frameId = 0;
    for (i = 0; i < this.active.length; i++) {
      p = this.active[i];
      if (p.t0 === null) p.t0 = now;
      leg = p.spec.route[p.i];
      if (p.fade) {                                                           // finished: fade out, then free the packet
        e = (now - p.fade) / 220;
        p.pk.el.style.opacity = String(Math.max(0, 1 - e));
        if (e >= 1) { p.pk.el.hidden = true; p.pk.busy = false; if (p.spec.onDone) { try { p.spec.onDone(); } catch (x) { /* ignore */ } } }
        else keep.push(p);
        continue;
      }
      e = Math.min(1, (now - p.t0) / Math.max(1, leg.hold !== undefined ? leg.hold : leg.ms));
      if (leg.wire) {
        w = this.wires[leg.wire];
        if (w && w.geom) {
          pt = C.pointAt(w.geom, leg.back ? 1 - C.easeInOut(e) : C.easeInOut(e));
          p.x = pt.x; p.y = pt.y;
        }
      }
      if (p.x !== undefined) {
        p.pk.el.style.transform = 'translate(' + Math.round(p.x - p.w / 2) + 'px,' + Math.round(p.y - p.h / 2) + 'px)';
        if (!p.shown) { p.pk.el.style.opacity = '1'; p.shown = true; }       // appears where it starts, not at 0,0
      }
      if (e >= 1) {
        try { if (p.spec.onStep) p.spec.onStep(p.i, p.api); } catch (x) { /* ignore */ }
        p.i++; p.t0 = now;
        if (p.i >= p.spec.route.length) { p.fade = now; }
      }
      keep.push(p);
    }
    this.active = keep;
    if (keep.length) this.frameId = root.requestAnimationFrame(function (t) { self._frame(t); });
  };

  /** Stop everything that is moving (hidden page, a dialog is open, the view changed). Callbacks do not run. */
  Stage.prototype.clear = function () {
    var i;
    if (this.frameId) { root.cancelAnimationFrame(this.frameId); this.frameId = 0; }
    for (i = 0; i < this.active.length; i++) { this.active[i].pk.el.hidden = true; this.active[i].pk.busy = false; }
    this.active = [];
  };
  Stage.prototype.pause = function () { this.paused = true; this.clear(); };
  Stage.prototype.resume = function () { this.paused = false; this.layout(); };

  Stage.prototype.destroy = function () {
    var i;
    this.clear();
    for (i = 0; i < this.timers.length; i++) root.clearTimeout(this.timers[i]);
    this.timers = [];
    root.removeEventListener('resize', this._onResize);
    if (this.ro) this.ro.disconnect();
  };

  // ====================================================================== number tickers
  /** Count a number up or down to `to` over `ms`; with reduced motion it just sets it. */
  function tickNumber(node, from, to, ms, format) {
    if (node._tick) { root.cancelAnimationFrame(node._tick); node._tick = 0; }
    if (reducedMotion() || from === to || document.hidden) { node.textContent = format(to); return; }
    var t0 = null;
    var step = function (now) {
      if (t0 === null) t0 = now;
      var e = Math.min(1, (now - t0) / ms);
      node.textContent = format(Math.round(C.lerp(from, to, C.easeInOut(e))));
      node._tick = e < 1 ? root.requestAnimationFrame(step) : 0;
    };
    node._tick = root.requestAnimationFrame(step);
  }

  root.PBLive = { Stage: Stage, tickNumber: tickNumber, reducedMotion: reducedMotion, svg: svg, div: div };
})(window);
