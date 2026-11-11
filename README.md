# cloak-setup

Reusable boilerplate for stealth-browser crawlers using
[CloakBrowser](https://github.com/CloakHQ/cloakbrowser). Forged in a real
collection crawl against CanLII, which sits behind DataDome — everything here
is something that broke once and was fixed.

| File | What it is |
|---|---|
| `cloak_setup.sh` | One-command installer for a fresh Ubuntu/Debian server. Idempotent. |
| `mini_crawler.py` | CloakBrowser template: probe → self-clear → fetch → store → resume. Strongest single-browser stealth; needs a (free) key. |
| `camoufox_crawler.py` | Same skeleton on **Camoufox** — open-source stealth Firefox, no key, no seat limit. **Verified live vs CanLII/DataDome 2026-11-11**: wall self-cleared in ~8s, 3/3 documents, zero mid-run walls. |
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
2. **One sticky residential session per run — and keep a good one.** DataDome
   judges the exit IP's reputation more than the browser. DataImpulse form:
   `http://USER__cr.ca;sessid.SESSIONID:PASS@gw.dataimpulse.com:823`. Once a
   session id lands on a clean IP, reuse it; fresh random sessions are a
   lottery, and a burned IP cannot be fixed by any browser version.
3. **Never lower the pace.** 4s between fetches was tolerated; faster gets
   rate-flagged mid-run.
4. **Walls clear themselves — wait, don't hammer.** DataDome's device-check
   interstitial passes on its own in a real browser on a residential IP:
   render it and wait up to ~45s, cool down 20s, continue. Hammering burns the
   IP. Capped recoveries per run; a wall that never clears means stop.
5. **Quit the browser cleanly.** A killed browser holds its licence seat for
   ~4 minutes; one concurrent browser per licence.

## Engine notes (measured)

- **Camoufox passed DataDome on the first try** (CanLII, 2026-11-11, the same
  sticky session the CloakBrowser runs used): probe wall self-cleared in ~8s,
  3/3 documents at 200, zero mid-run walls — where CloakBrowser hit walls
  mid-run on the identical session. Launch config that passed: `headless=True,
  geoip=True, humanize=True` + the sticky proxy. Keep the canvas/WebGL noise
  the `LeakWarning` tells you about — it is intended and passed as-is.
- **CloakBrowser** hit mid-run walls on the same session that Camoufox did
  not; both cleared by waiting. Prefer Camoufox as the default engine; keep
  CloakBrowser for targets that specifically defeat Firefox fingerprints.
- **DataDome blocks are mostly IP reputation** (their maintainers say the
  same): one known-good sticky session beats everything else you can tune.

## What does NOT work (measured 2026-11-10/11)

- **Plain HTTP clients, always** — 403 with a perfect Chrome TLS fingerprint
  (`curl_cffi`), even through residential proxies. A real browser is required.
- **2Captcha / Capsolver for DataDome** — their workers solve from their own
  pool and ignore the user-supplied proxy, so the returned cookie is bound to
  the wrong IP and never clears. Self-clearing replaced it entirely.
- **Datacenter IPs, even with a real browser** — you need the residential
  proxy; the browser alone is not enough.
