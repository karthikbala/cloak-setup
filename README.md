# cloak-setup

Reusable boilerplate for stealth-browser crawlers using
[CloakBrowser](https://github.com/CloakHQ/cloakbrowser). Forged in a real
collection crawl against CanLII, which sits behind DataDome — everything here
is something that broke once and was fixed.

| File | What it is |
|---|---|
| `cloak_setup.sh` | One-command installer for a fresh Ubuntu/Debian server. Idempotent. |
| `mini_crawler.py` | CloakBrowser template: probe → self-clear → fetch → store → resume, with session redraw on burnout and planned rotation. Strongest single-browser stealth; needs a (free) key. |
| `camoufox_crawler.py` | Same skeleton on **Camoufox** — open-source stealth Firefox, no key, no seat limit. **Verified at scale vs CanLII/DataDome 2026-11-11/12**: 1000 documents, 5 parallel browsers, session rotation + redraw. |
| `replay_crawler.py` | API replay twin: mint once, then POST a job list with in-page `fetch()` through the same session. **Verified live vs Expedia/DataDome 2026-11-11**: 100s of priced responses at 200 on IPs where every `curl_cffi` replay was denied. |
| `run_parallel.sh` | N-worker fan-out with a per-worker supervisor; feeds each worker a CHUNK url file and its own sticky session. |
| `AGENTS.md` | **For coding agents**: the rules that keep a crawler unblocked. Read before writing code here. |

## Quickstart on a new server

```sh
sudo ./cloak_setup.sh                 # deps, fonts, venv, diagnostics

# Engine A: CloakBrowser (strongest Chromium stealth, free key, 1 browser)
export CLOAKBROWSER_LICENSE_KEY=...   # free key: https://cloakbrowser.dev/free
cp mini_crawler.py my_crawler.py      # then edit TARGET / URLS / storage

# Engine B: Camoufox (open source, no key, unlimited browsers)
/opt/cloak/venv/bin/pip install 'camoufox[geoip]'
/opt/cloak/venv/bin/camoufox fetch
python3 -m playwright install-deps firefox
cp camoufox_crawler.py my_crawler.py
```

Pick by need: one hardened browser → CloakBrowser; many browsers or no
vendor key → Camoufox. The proxy, pacing and wall rules are identical for
both — that is where the blocking game is actually decided.

## The five rules (each one cost us a debugging session)

1. **Persona must match real fonts.** On Linux CloakBrowser spoofs Windows by
   default; without the *real* Windows fonts installed, font-fingerprinting
   walls (DataDome) hard-block with no captcha. Either copy
   `C:\Windows\Fonts\*` from a real Windows machine, or spoof `macos` and copy
   a real Mac's `/System/Library/Fonts`. Fonts must live in a fontconfig path
   (`/usr/local/share/fonts/...`) — a random directory is never scanned.
   Diagnose with `cloakbrowser info`.
2. **One sticky residential session per run — and rotate it before it burns.** DataDome
   judges the exit IP's reputation more than the browser. DataImpulse form:
   `http://USER__cr.ca;sessid.SESSIONID:PASS@gw.dataimpulse.com:823`. Once a
   session id lands on a clean IP, reuse it; fresh random sessions are a
   lottery (measured pass rate ~3/5), and a burned IP cannot be fixed by any
   browser version. But sessions also *age out under load*: in the 1000-doc
   run, all five workers hit walls at ~60 docs per session. Rotate the
   session every ~40 docs (templates: `ROTATE_EVERY`) and mid-run walls
   mostly disappear. Re-validate a reused pin first: DataImpulse sticky pins
   were observed rotating to a different IP in under an hour (2 of 3 pins
   moved in one hour), which silently breaks cookie/IP binding. A cheap
   `ipinfo.io` lookup through the proxy before launch catches it.
3. **Never lower the pace.** 4s between fetches was tolerated; faster gets
   rate-flagged mid-run.
4. **Walls clear themselves — and when one doesn't, redraw, don't die.** DataDome's
   device-check interstitial passes on its own in a real browser on a
   residential IP: render it and wait up to ~45s, cool down 20s, continue
   once. If it still hasn't cleared, the session is burned: redraw the
   session (90s cooldown) and resume from `captures.jsonl` — the completed
   work is never repeated and none is lost. Do not hammer the same identity
   and do not treat a stubborn wall as fatal: in the completion run every
   worker needed one redraw, and a chunk that redraws kept going to 89 docs.
5. **Quit the browser cleanly.** A killed browser holds its licence seat for
   ~4 minutes; one concurrent browser per licence.
6. **Fan out under a supervisor.** At 5+ workers, something always exits:
   crashes, draw exhaustion, a hostile proxy pool. Give each worker its own
   chunk (`CHUNK` env) and sticky session, and relaunch a dead worker once
   after a cooldown (`run_parallel.sh`). The resume-from-captures design
   makes relaunches free — in the completion run, 2 of 5 chunks needed one.

## Engine notes (measured)

- **Camoufox passed DataDome on the first try** (CanLII, 2026-11-11, the same
  sticky session the CloakBrowser runs used): probe wall self-cleared in ~8s,
  3/3 documents at 200, zero mid-run walls — where CloakBrowser hit walls
  mid-run on the identical session. Launch config that passed: `headless=True,
  geoip=True, humanize=True` + the sticky proxy. Keep the canvas/WebGL noise
  the `LeakWarning` tells you about — it is intended and passed as-is.
- **At scale (1000 decisions, 5 parallel browsers, 2026-11-11/12)** the wall
  picture changed: all workers eventually hit mid-run walls at ~60 docs per
  session, and one chunk lost the fresh-draw lottery 5 times in a row. The
  fixes that completed the run — rotation every 40 docs, redraw-on-stubborn-
  wall with resume, MAX_DRAWS=8, supervised relaunch — are what the templates
  now encode. Walls are a session-ageing signal, not a target signal.
- **CloakBrowser** hit mid-run walls on the same session that Camoufox did
  not; both cleared by waiting. Prefer Camoufox as the default engine; keep
  CloakBrowser for targets that specifically defeat Firefox fingerprints.
- **DataDome blocks are mostly IP reputation** (their maintainers say the
  same): one known-good sticky session beats everything else you can tune —
  and reputation degrades under load, hence rotation.

## Replaying API calls (Expedia/DataDome, measured 2026-11-11)

For targets where the data lives behind an API the page calls (a GraphQL
search feed, a JSON endpoint), do not replay that API from a plain HTTP
client — replay it **inside the page**:

1. Mint the session in a real browser (probe the site, let any wall
   self-clear), with a request listener capturing the exact API request the
   page makes: URL, headers and body.
2. Fire each job with `page.evaluate(fetch(...))` on the same origin,
   `credentials: 'include'`, replaying the captured headers (minus forbidden
   ones: host, content-length, cookie, origin, referer, user-agent).
3. Replaying with only `content-type` was rejected with 403 — the captured
   headers are part of what the edge accepts. A 429 burns the session; stop
   and resample rather than hammering.

Measured on Expedia: ~100 LodgingCard results per request in ~2s, 100% at
200, on the same sticky IPs where `curl_cffi` — with a perfect Chrome TLS
fingerprint — was denied every time with a DataDome challenge page.

**Curl replay acceptance is per provider pool and drifts over time.** The
same Expedia replay that returned 200 through one residential provider in
early November was hard-denied through another days later — chrome and
firefox TLS profiles alike. If a curl replay worked last week, re-verify it
before building on it; the browser-side pattern above is the one that held.

## What does NOT work (measured 2026-11-10/11)

- **Plain HTTP clients, always** — 403 with a perfect Chrome TLS fingerprint
  (`curl_cffi`), even through residential proxies. A real browser is required.
  Caveat learned later on Expedia: a curl replay can work from one residential
  provider pool and be hard-denied from another days later — never build a
  pipeline on it. See "Replaying API calls" below for the pattern that held.
- **2Captcha / Capsolver for DataDome** — their workers solve from their own
  pool and ignore the user-supplied proxy, so the returned cookie is bound to
  the wrong IP and never clears. Self-clearing replaced it entirely.
- **Datacenter IPs, even with a real browser** — you need the residential
  proxy; the browser alone is not enough.
