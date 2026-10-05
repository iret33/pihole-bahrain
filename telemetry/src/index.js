// The Worker's entry point (wrangler.toml: main). A Worker module may export nothing but its handlers, so this file
// only does that. Everything else is in worker.js.

import { createWorker } from './worker.js';

export default createWorker();
