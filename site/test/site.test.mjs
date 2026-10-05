// The site's text, settings and markup: both languages complete and in step with the page, config.js valid,
// every file the page names present, readable colours, and the language chosen before the first paint.

import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { ROOT, SITE, loadScripts, read } from './helpers.mjs';

const win = loadScripts(['config.js', 'strings.js', 'lib.js']);
const STR = win.SINKO_STRINGS;
const CONFIG = win.SINKO_CONFIG;
const Lib = win.SinkoLib;
const html = read('index.html');
const appJs = read('app.js');
const css = read('style.css');

const ARABIC = /[\u0600-\u06FF]/;
const placeholders = (text) => (text.match(/\{\w+\}/g) || []).sort();
const decode = (text) => text.replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
// Words that are the same in both languages on purpose (a product name, or the other language's name on the switch).
const SAME_IN_BOTH = new Set(['footGitHub']);
const NOT_ARABIC = new Set(['langSwitch', 'footGitHub']);       // langSwitch: English reads "العربية", Arabic reads "English"

// ---------------------------------------------------------------- the two languages
test('both languages have exactly the same keys', () => {
  assert.deepEqual(Object.keys(STR).sort(), ['ar', 'en']);
  assert.deepEqual(Object.keys(STR.ar).sort(), Object.keys(STR.en).sort());
});

test('every string is real text, with the same {placeholders} in both languages', () => {
  for (const lang of ['en', 'ar']) {
    for (const [key, value] of Object.entries(STR[lang])) {
      assert.equal(typeof value, 'string', `${lang}.${key}`);
      assert.ok(value.length > 0, `${lang}.${key} is empty`);
      assert.equal(value, value.trim(), `${lang}.${key} has spaces around it`);
      assert.doesNotMatch(value, /[<>&]/, `${lang}.${key} holds markup: text only, so that the page and strings.js agree`);
      assert.doesNotMatch(value, /\s{2,}/, `${lang}.${key} has a double space`);
    }
  }
  for (const key of Object.keys(STR.en)) assert.deepEqual(placeholders(STR.ar[key]), placeholders(STR.en[key]), key);
});

test('no Arabic is English by mistake, and no English is Arabic', () => {
  for (const key of Object.keys(STR.en)) {
    if (!SAME_IN_BOTH.has(key)) assert.notEqual(STR.ar[key], STR.en[key], `${key} is the same in both languages`);
    if (!NOT_ARABIC.has(key)) {
      assert.match(STR.ar[key], ARABIC, `ar.${key} has no Arabic letters`);
      assert.doesNotMatch(STR.en[key], ARABIC, `en.${key} has Arabic letters`);
    }
  }
  assert.match(STR.en.langSwitch, ARABIC);                      // the button that leaves English names Arabic in Arabic
  assert.equal(STR.ar.langSwitch, 'English');
});

test('the privacy answer is true: the box asks an upstream DNS service, and nothing says that lookups never leave the box', () => {
  for (const lang of ['en', 'ar']) {
    assert.match(STR[lang].a2, /Cloudflare for Families/, lang);
    assert.match(STR[lang].a2, /DNS/, lang);
  }
  assert.match(STR.en.a2, /upstream DNS service/);
  assert.match(STR.en.a2, /site names and your home’s internet address/);
  // What a family looks up does leave the box (Pi-hole passes it to its upstream DNS service), so none of these may come back.
  const claims = [/stays? on the box/i, /nothing leaves the box/i, /never leaves? your box/i, /looks? up[^.]*\bstays?\b/i, /nothing (else )?is collected/i];
  for (const [key, value] of Object.entries(STR.en)) for (const claim of claims) assert.doesNotMatch(value, claim, `en.${key}`);
  for (const claim of claims.slice(0, 3)) assert.doesNotMatch(html, claim);
  // The counter keeps a little more than the three things the box sends, and the note next to the numbers says so.
  assert.match(STR.en.countsNote, /country/);
  assert.match(STR.ar.countsNote, /البلد/);
  // A ready-made box has nothing to uninstall, and the answer about removing it must not tell its owner to type a command.
  assert.match(STR.en.a5, /ready-made box has nothing to remove/);
});

test('one Arabic tagline, in the title and under the name, and the old variants are gone', () => {
  assert.equal(STR.ar.heroTagline, 'إنترنت هادئ للعائلة');
  assert.equal(STR.ar.docTitle, `سينكو · ${STR.ar.heroTagline}`);
  const everything = read('strings.js') + html + read('app.js');
  for (const old of ['إنترنت هادئ لعائلتك', 'إنترنت العائلة']) assert.equal(everything.includes(old), false, old);
});

test('Arabic uses the terms the parent page itself shows', () => {
  const all = Object.values(STR.ar).join('\n');
  for (const term of ['وقت الدراسة', 'وقت النوم', 'الصورة الحيّة', 'صندوقي', 'حدّث الآن', 'صندوق العائلة']) assert.ok(all.includes(term), term);
  // The Arabic name of the product is written one way everywhere.
  assert.doesNotMatch(all, /سنكو|سينكوو/);
  // Latin digits, as on the box.
  assert.doesNotMatch(all, /[\u0660-\u0669]/);
});

test('every key the page uses exists, and every key is used', () => {
  const used = new Set();
  for (const m of html.matchAll(/data-i18n="(\w+)"/g)) used.add(m[1]);
  for (const m of html.matchAll(/data-i18n-attr="([^"]+)"/g)) for (const pair of m[1].split(';')) used.add(pair.split(':')[1].trim());
  for (const m of appJs.matchAll(/\bt\('(\w+)'\)/g)) used.add(m[1]);
  for (const key of used) assert.ok(key in STR.en, `the page uses "${key}", which strings.js does not have`);
  for (const key of Object.keys(STR.en)) assert.ok(used.has(key), `"${key}" is in strings.js but nothing uses it`);
});

test('the English text written in index.html is the English in strings.js', () => {
  let checked = 0;
  for (const m of html.matchAll(/<(\w+)\b[^>]*\bdata-i18n="(\w+)"[^>]*>([^<]*)</g)) {
    assert.equal(decode(m[3]), STR.en[m[2]], `<${m[1]} data-i18n="${m[2]}">`);
    checked += 1;
  }
  for (const m of html.matchAll(/<[^>]*\bdata-i18n-attr="([^"]+)"[^>]*>/g)) {
    for (const pair of m[1].split(';')) {
      const [attr, key] = pair.split(':').map((s) => s.trim());
      const value = new RegExp(`\\s${attr}="([^"]*)"`).exec(m[0]);
      assert.ok(value, `${key}: the tag has no ${attr}`);
      assert.equal(decode(value[1]), STR.en[key], `${attr} for ${key}`);
      checked += 1;
    }
  }
  assert.ok(checked > 60, `only ${checked} texts checked`);
  // What search engines and link previews read.
  assert.equal(decode(/property="og:title" content="([^"]*)"/.exec(html)[1]), STR.en.docTitle);
  assert.equal(decode(/property="og:description" content="([^"]*)"/.exec(html)[1]), STR.en.metaDescription);
  assert.equal(decode(/<noscript><img [^>]*alt="([^"]*)"/.exec(html)[1]), STR.en.heroImgAlt);
});

// ---------------------------------------------------------------- settings
test('config.js is valid', () => {
  assert.deepEqual(Lib.configProblems(CONFIG), []);
  assert.ok(Object.isFrozen(CONFIG));
  assert.deepEqual(Object.keys(CONFIG).sort(), ['buyUrl', 'repo', 'statsUrl', 'supportUrl']);
});

test('the install command in the page is the one for the configured repository', () => {
  const written = /<code id="installCmd"[^>]*>([^<]*)</.exec(html)[1];
  assert.equal(written, Lib.installCommand(CONFIG.repo));
  assert.ok(html.includes(`https://github.com/${CONFIG.repo}`), 'links to the repository');
  assert.ok(html.includes(`https://github.com/${CONFIG.repo}/blob/master/docs/privacy.md`), 'the privacy statement');
});

// ---------------------------------------------------------------- markup
test('the page is sound: one h1, unique ids, working anchors, alt text, no stray placeholder', () => {
  assert.match(html, /^<!doctype html>\n<html lang="en" dir="ltr">/);
  assert.equal((html.match(/<h1\b/g) || []).length, 1);
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]);
  assert.equal(new Set(ids).size, ids.length, 'duplicate id');
  for (const m of html.matchAll(/href="#([^"]+)"/g)) assert.ok(ids.includes(m[1]), `no element with id ${m[1]}`);
  for (const m of html.matchAll(/<img\b[^>]*>/g)) assert.match(m[0], /\balt="/, m[0]);
  assert.match(html, /<meta name="viewport" content="width=device-width, initial-scale=1">/);
  // The three absolute addresses crawlers need; assemble.sh fills them in.
  assert.deepEqual([...html.matchAll(/__SITE_URL__[^"]*/g)].map((m) => m[0]), ['__SITE_URL__/', '__SITE_URL__/', '__SITE_URL__/img/social-preview.png']);
  assert.match(html, /<link rel="canonical" href="__SITE_URL__\/">/);
  assert.match(html, /property="og:image" content="__SITE_URL__\/img\/social-preview\.png"/);
  assert.match(html, /<link rel="icon" href="icon\.svg"/);
  assert.doesNotMatch(html, /\s(?:style|on\w+)="/, 'inline styles and handlers');
});

test('every file the page names exists (here, or copied in at deploy time) and addresses are relative', () => {
  const deployTime = (ref) => ref === 'icon.svg' || /^img\/[\w.-]+\.(png|jpg|webp|svg)$/.test(ref) || /^fonts\//.test(ref);
  const refs = [...html.matchAll(/\b(?:src|href)="([^"#][^"]*)"/g)].map((m) => m[1]).filter((r) => !/^(https?:|mailto:|__SITE_URL__)/.test(r));
  assert.ok(refs.length >= 6);
  for (const ref of refs) {
    assert.doesNotMatch(ref, /^\//, `${ref} is root-relative and would break under /sinko/`);
    assert.ok(fs.existsSync(path.join(SITE, ref)) || deployTime(ref), `${ref} is neither in site/ nor a deploy-time copy`);
  }
  assert.ok(appJs.includes("'img/'"), 'app.js builds image names relative to the page');
  for (const m of css.matchAll(/url\(([^)]+)\)/g)) {
    assert.match(m[1], /^fonts\/[\w-]+\.woff2$/, m[1]);
    assert.ok(fs.existsSync(path.join(ROOT, 'web', m[1])), `${m[1]} is not in web/fonts`);
  }
  assert.ok(fs.existsSync(path.join(ROOT, 'web', 'icon.svg')));
  assert.ok(fs.existsSync(path.join(ROOT, 'web', 'fonts', 'OFL.txt')), 'the font licence travels with the fonts');
  for (const script of [...html.matchAll(/<script src="([^"]+)"/g)].map((m) => m[1])) assert.ok(fs.existsSync(path.join(SITE, script)), script);
});

test('the screenshots the page asks for are the ones the plan names', () => {
  const images = [...html.matchAll(/(?:src)="(img\/[^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual([...new Set(images)].sort(), ['img/box-en.png', 'img/live-en.png', 'img/panel-ar.png', 'img/panel-en.png']);
});

// ---------------------------------------------------------------- style
function luminance(hex) {
  const channel = (i) => {
    const c = parseInt(hex.slice(1 + 2 * i, 3 + 2 * i), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(0) + 0.7152 * channel(1) + 0.0722 * channel(2);
}
const contrast = (a, b) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};
function tokens(name) {
  const block = new RegExp(`/\\* @tokens ${name} \\*/([\\s\\S]*?)/\\* @end \\*/`).exec(css);
  assert.ok(block, `no ${name} tokens`);
  return Object.fromEntries([...block[1].matchAll(/(--[\w-]+):\s*(#[0-9A-Fa-f]{6})\b/g)].map((m) => [m[1], m[2]]));
}

test('text and controls are readable in the light and the dark colours (WCAG AA)', () => {
  const text = [
    ['--ink', '--bg'], ['--ink', '--surface'], ['--ink', '--brand-soft'], ['--ink', '--bg-top'],
    ['--ink-2', '--bg'], ['--ink-2', '--surface'], ['--ink-2', '--brand-soft'], ['--ink-2', '--bg-top'],
    ['--brand-ink', '--bg'], ['--brand-ink', '--surface'], ['--brand-ink', '--brand-soft'], ['--brand-ink', '--bg-top'],
    ['--on-btn', '--btn'], ['--sand-ink', '--sand'],
  ];
  for (const scheme of ['light', 'dark']) {
    const t = tokens(scheme);
    for (const [fg, bg] of text) {
      assert.ok(t[fg] && t[bg], `${scheme}: ${fg} or ${bg} is missing`);
      assert.ok(contrast(t[fg], t[bg]) >= 4.5, `${scheme}: ${fg} on ${bg} is ${contrast(t[fg], t[bg]).toFixed(2)}:1`);
    }
    assert.ok(contrast(t['--brand'], t['--bg']) >= 3, `${scheme}: the brand colour is a graphic on the page and needs 3:1`);
    assert.ok(contrast(t['--focus'], t['--bg']) >= 3 && contrast(t['--focus'], t['--surface']) >= 3, `${scheme}: focus ring`);
  }
  assert.equal(tokens('light')['--brand'].toUpperCase(), '#0F766E');   // the brand colour
});

test('one stylesheet for both directions: logical properties, dark colours, reduced motion', () => {
  const rules = css.replace(/\/\*[\s\S]*?\*\//g, '');
  assert.doesNotMatch(rules, /(?:margin|padding|border)-(?:left|right)\b/, 'a physical side');
  assert.doesNotMatch(rules, /(?:^|[;{\s])(?:left|right)\s*:/, 'a physical offset');
  assert.doesNotMatch(rules, /text-align:\s*(?:right)\b/);
  assert.match(css, /@media \(prefers-color-scheme:dark\)/);
  assert.match(css, /@media \(prefers-reduced-motion:reduce\)/);
  assert.match(css, /@media \(prefers-reduced-motion:no-preference\)/);
  assert.match(css, /\[hidden\]\{display:none!important\}/);
  assert.match(css, /font-display:swap/);
  assert.match(css, /IBM Plex Sans Arabic/);
});

// ---------------------------------------------------------------- language, before the first paint
function boot({ search = '', stored = null, storage = 'ok', languages = ['en-US'], language } = {}) {
  const script = /<script id="lang-boot">([\s\S]*?)<\/script>/.exec(html)[1];
  const docEl = {};
  const links = [];
  const document = {
    documentElement: docEl,
    head: { appendChild: (el) => links.push(el) },
    createElement: () => ({}),
  };
  const localStorage = {
    getItem() { if (storage === 'throws') throw new Error('blocked'); return stored; },
  };
  const navigator = { languages, language };
  new Function('document', 'location', 'localStorage', 'navigator', 'URLSearchParams', script)(document, { search }, localStorage, navigator, URLSearchParams);
  return { lang: docEl.lang, dir: docEl.dir, preload: links.map((l) => l.href) };
}

test('the language is picked before anything is drawn: address, then saved choice, then the browser', () => {
  assert.deepEqual(boot(), { lang: 'en', dir: 'ltr', preload: ['img/panel-en.png'] });
  assert.deepEqual(boot({ languages: ['ar-BH', 'en'] }), { lang: 'ar', dir: 'rtl', preload: ['img/panel-ar.png'] });
  assert.equal(boot({ languages: ['ar'] }).lang, 'ar');
  assert.equal(boot({ languages: ['en-US', 'ar'] }).lang, 'en');           // the first language is the one that counts
  assert.equal(boot({ languages: [], language: 'ar-SA' }).lang, 'ar');
  assert.equal(boot({ languages: undefined, language: undefined }).lang, 'en');
  assert.equal(boot({ stored: 'en', languages: ['ar'] }).lang, 'en');      // a saved choice beats the browser
  assert.equal(boot({ stored: 'ar', languages: ['en'] }).lang, 'ar');
  assert.equal(boot({ search: '?lang=ar', stored: 'en' }).lang, 'ar');     // the address beats a saved choice
  assert.equal(boot({ search: '?x=1&lang=en', stored: 'ar' }).lang, 'en');
  assert.equal(boot({ search: '?lang=fr', stored: 'ar' }).lang, 'ar');     // an unknown value is ignored
  assert.equal(boot({ stored: 'klingon', languages: ['ar'] }).lang, 'ar');
  assert.equal(boot({ storage: 'throws', languages: ['ar'] }).lang, 'ar'); // storage blocked: still works
  assert.equal(boot({ languages: ['arabic-ish'] }).lang, 'en');
});

// ---------------------------------------------------------------- the folder that is published
test('assemble.sh builds the published folder, and refuses an address it cannot trust', (t) => {
  if (spawnSync('bash', ['--version']).status !== 0) return t.skip('bash is not available');
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'sinko-site-'));
  t.after(() => fs.rmSync(tmp, { recursive: true, force: true }));
  // A copy of just what the script reads, so that docs/img can be present or absent as the test wants.
  const root = path.join(tmp, 'repo');
  fs.cpSync(SITE, path.join(root, 'site'), { recursive: true });
  fs.cpSync(path.join(ROOT, 'web', 'fonts'), path.join(root, 'web', 'fonts'), { recursive: true });
  fs.copyFileSync(path.join(ROOT, 'web', 'icon.svg'), path.join(root, 'web', 'icon.svg'));
  const run = (out, base) => spawnSync('bash', [path.join(root, 'site', 'assemble.sh'), out, ...(base === undefined ? [] : [base])], { encoding: 'utf8' });

  const out = path.join(tmp, '_site');
  let r = run(out, 'https://iret33.github.io/sinko/');                     // no pictures yet: a warning, not a failure
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /::warning::docs\/img has no pictures/);
  const built = fs.readFileSync(path.join(out, 'index.html'), 'utf8');
  assert.match(built, /<link rel="canonical" href="https:\/\/iret33\.github\.io\/sinko\/">/);
  assert.match(built, /property="og:image" content="https:\/\/iret33\.github\.io\/sinko\/img\/social-preview\.png"/);
  assert.doesNotMatch(built, /__SITE_URL__/);
  for (const file of ['index.html', 'style.css', 'app.js', 'lib.js', 'strings.js', 'config.js', 'icon.svg', 'fonts/OFL.txt', 'fonts/plex-arabic-arabic-400.woff2', '.nojekyll']) {
    assert.ok(fs.existsSync(path.join(out, file)), file);
  }
  for (const file of ['test', 'assemble.sh', 'README.md']) assert.equal(fs.existsSync(path.join(out, file)), false, `${file} must not be published`);
  assert.deepEqual(fs.readdirSync(path.join(out, 'img')), []);

  fs.mkdirSync(path.join(root, 'docs', 'img'), { recursive: true });
  for (const name of ['panel-en.png', 'social-preview.png']) fs.writeFileSync(path.join(root, 'docs', 'img', name), 'x');
  fs.writeFileSync(path.join(root, 'docs', 'img', 'README.md'), 'notes for the people who make the brand files');
  r = run(out);                                                            // rebuilt from scratch, base empty (local preview)
  assert.equal(r.status, 0, r.stderr);
  // The screenshots arrive later than the site: each missing one is named in the log, and none of them fails the build.
  assert.deepEqual([...r.stdout.matchAll(/::warning::docs\/img\/(\S+) is missing/g)].map((m) => m[1]), ['panel-ar.png', 'live-en.png', 'box-en.png']);
  assert.doesNotMatch(r.stdout, /no pictures/);
  // Only pictures are published: the README that lives in docs/img is not for visitors.
  assert.deepEqual(fs.readdirSync(path.join(out, 'img')).sort(), ['panel-en.png', 'social-preview.png']);
  assert.match(fs.readFileSync(path.join(out, 'index.html'), 'utf8'), /property="og:image" content="\/img\/social-preview\.png"/);

  for (const bad of ['http://x.example', 'https://x.example|evil', 'https://x.example/a b', 'https://x.example"onload=1', 'javascript:alert(1)', 'https://x.example/&']) {
    r = run(out, bad);
    assert.notEqual(r.status, 0, bad);
    assert.match(r.stderr, /not a plain https address/);
  }
  r = spawnSync('bash', [path.join(root, 'site', 'assemble.sh')], { encoding: 'utf8' });
  assert.notEqual(r.status, 0);
  assert.match(r.stderr, /usage/);
});
