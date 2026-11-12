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
- Match locale/timezone to the proxy's country. On Camoufox use `geoip=True`
  (it resolves timezone, locale and WebRTC from the proxy exit); on
  CloakBrowser set them explicitly or pass `geoip=True` with the geoip extra.

## Engine rules

- Default to Camoufox: proven against DataDome on first try (2026-11-11),
  open source, no licence seat, free concurrency. Reach for CloakBrowser only
  when a target specifically defeats Firefox fingerprints.
- Camoufox launch that passed: `Camoufox(headless=True, geoip=True,
  humanize=True, proxy=STICKY)`. Keep canvas/WebGL noise on (the LeakWarning
  is expected); do not pass `canvas_noise=False` to silence it.
- A fresh session id is a draw from the IP lottery: measured 2026-11-11 with
  5 parallel browsers on 5 fresh draws, only 3 passed the probe — the others
  redrew new sessions and passed within 1-3 redraws. Build the redraw in
  (see the template's probe loop); never hammer a burned IP. Budget for the
  lottery: MAX_DRAWS=8 with a 90s cooldown after failed draws (measured
  2026-11-12 — one worker lost 5 draws in a row and still completed its
  chunk on a later draw).
- Sessions age out under load: in the 1000-doc completion run (2026-11-12,
  5 browsers), every worker hit a wall at ~60 docs per session. Rotate the
  session every ~40 docs (`ROTATE_EVERY`) — planned rotation replaced
  mid-run walls with a cheap, clean restart. Each browser must have its own
  sticky proxy session — sharing one session across browsers spends the IP's
  reputation collectively.

## Behaviour rules

- Never lower the fetch pace. The template's 4s pace was tolerated; walls that
  appear anyway are recovered by waiting, not by speeding up or retrying fast.
- When a wall appears: render it (navigate to the URL), wait up to ~45s for
  the device check to clear itself, cool down 20s, retry once, then continue.
  If it still has not cleared, the session — not the target — is burned:
  redraw the session (90s cooldown) and resume the chunk from
  `captures.jsonl`. Resume is free by design, so a redraw repeats nothing
  and loses nothing. Keep the wall page saved as evidence. Do not solve and
  do not hammer one identity hoping it recovers. (This supersedes the
  earlier "never rotate IPs mid-run" rule, which was written before the
  completion run showed session ageing is the normal mid-run failure.)
- Do not wire in captcha-solving services for DataDome: measured on
  2026-11-10, 2Captcha and Capsolver workers solve from their own pool and
  ignore the user-supplied proxy, so the returned cookie is bound to the wrong
  IP and is worthless. The self-clear mechanism replaced them.
- Close the browser cleanly (`ctx.close()` in a `finally`). A killed browser
  holds its licence seat ~4 minutes and a crash-loop burns that seat
  repeatedly.

## Code rules

- Keep crawlers in the template shape: probe → fetch loop → store → resume,
  with wall handling in one place (`fetch_with_wall_retry` → `wait_self_clear`)
  and the draw loop in `main` (redraw, rotate, cooldown). Do not add
  concurrency beyond one browser per sticky proxy session.
- For parallel runs, fan out with `run_parallel.sh`: each worker gets a CHUNK
  url file, its own sticky SESSID, and one supervisor relaunch. Do not share
  a session or a chunk across workers.
- Store fetched bytes content-addressed by SHA-256 and log one JSON line per
  attempt to `captures.jsonl` with the HTTP status — resume, audits and
  provenance depend on it.
- Secrets (proxy credentials, licence keys) only via environment variables or
  root-600 files. Never commit them, never log them, never put them in
  `run.json` or capture records.
