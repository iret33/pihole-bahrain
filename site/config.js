/* Settings of the Sinko project site. A plain script with no build step: change a value, push, and the site updates.
   Everything in this file is public (the browser downloads it), so never put a secret here.
   site/test/site.test.js checks that this file is valid. */
window.SINKO_CONFIG = Object.freeze({
  /* The GitHub repository, "owner/name". It gives the install command, the links to GitHub (code, privacy statement,
     licence) and, when statsUrl is empty, the download count (the sum of the release downloads). */
  repo: "iret33/sinko",

  /* The address of the anonymous counter (the Worker in telemetry/), https, no trailing slash, for example
     "https://sinko-counter.example.workers.dev". The site then shows how many boxes are online and the download total
     that the counter reports. Empty: the site shows only the download count, taken from GitHub. If the counter or
     GitHub cannot be reached, the numbers are simply not shown. */
  statsUrl: "https://sinko-counter.sabdulla.workers.dev",

  /* Where to buy a ready-made box (a shop page), https. Empty: the "Get a ready-made box" button is hidden and the
     card says that the box is not on sale yet. */
  buyUrl: "",

  /* Where people get help (a page, or "mailto:name@example.org"). Empty: the "Support" link in the footer is hidden. */
  supportUrl: ""
});
