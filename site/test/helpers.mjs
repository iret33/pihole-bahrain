// Shared by the site tests. The site's browser scripts are plain scripts that set window.X, so each one is run as a
// function of `window` (in this realm, so that what they return compares equal to what a test writes) and read back.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const SITE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const ROOT = path.resolve(SITE, '..');

export const read = (...parts) => fs.readFileSync(path.join(SITE, ...parts), 'utf8');

export function loadScripts(names) {
  const window = {};
  for (const name of names) new Function('window', read(name))(window);
  return window;
}
