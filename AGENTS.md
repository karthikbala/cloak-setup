# AGENTS.md — rules for coding agents working in this repo

This repo is boilerplate for stealth-browser crawlers. It was extracted from a
production crawl (CanLII behind DataDome, 2026-11-10) where every rule below
was learned by breaking. When you write or modify a crawler here:

## Setup rules

- Run `sudo ./cloak_setup.sh` on any new Ubuntu/Debian server before anything
  else. It is idempotent. It installs Chromium's system libraries, baseline
  canvas/emoji fonts, and a venv with `cloakbrowser`.
- After setup, run `cloakbrowser info`. If it reports persona fonts missing,
  STOP and fix fonts before writing any crawler code — a mismatched persona
  produces hard blocks with no captcha and no error message to guide you.
- Real fonts cannot come from apt (Microsoft-proprietary). Copy them from a
  real machine: `C:\Windows\Fonts\*` for the Windows persona, a Mac's
  `/System/Library/Fonts` for the macos persona, into
  `/usr/local/share/fonts/<persona>/`, then `fc-cache -f`. On a Mac-adjacent
  workflow, copying the Mac fonts and spoofing `macos` is the proven path.

## Proxy rules

- Always launch through a sticky residential proxy. DataDome judges exit-IP
  reputation more than the browser; a datacenter IP fails even with a perfect
  browser, and plain HTTP clients fail even with a perfect IP.
- DataImpulse form: `http://USER__cr.ca;sessid.SESSIONID:PASS@gw.dataimpulse.com:823`.
  The `sessid` pins one exit IP. Keep a stable session id per crawler, not a
  fresh random one per run: IP reputation accrues to the session, and a
  known-good session is the single most valuable runtime asset. Credentials
  come from the environment or a root-600 secrets file — never hardcode them.
- Match locale/timezone to the proxy's country (`geoip=True`, or set them
  explicitly as the template does).

## Behaviour rules

- Never lower the fetch pace. The template's 4s pace was tolerated; walls that
  appear anyway are recovered by waiting, not by speeding up or retrying fast.
- When a wall appears: render it (navigate to the URL), wait up to ~45s for
  the device check to clear itself, cool down 20s, retry once, then continue.
  Cap total recoveries per run (default 5) and STOP with the wall page saved
  as evidence when the cap is spent. Do not solve, do not hammer, do not
  rotate IPs mid-run to dodge a wall.
- Do not wire in captcha-solving services for DataDome: measured on
  2026-11-10, 2Captcha and Capsolver workers solve from their own pool and
  ignore the user-supplied proxy, so the returned cookie is bound to the wrong
  IP and is worthless. The self-clear mechanism replaced them.
- Close the browser cleanly (`ctx.close()` in a `finally`). A killed browser
  holds its licence seat ~4 minutes and a crash-loop burns that seat
  repeatedly.

## Code rules

- Keep crawlers in the template shape: probe → fetch loop → store → resume,
  with the wall handling in one place (`wait_self_clear`). Do not add
  concurrency on CloakBrowser (one licence seat); on Camoufox, scale browsers
  only with separate sticky proxy sessions — never share one session across
  browsers, or the IP's reputation gets spent by all of them at once.
- Store fetched bytes content-addressed by SHA-256 and log one JSON line per
  attempt to `captures.jsonl` — resume and audits depend on it.
- Secrets (proxy credentials, licence keys) only via environment variables or
  root-600 files. Never commit them, never log them, never put them in
  `run.json` or capture records.
