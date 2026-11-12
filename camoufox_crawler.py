#!/usr/bin/env python3
"""Camoufox crawler template — the open-source engine twin of mini_crawler.py.

Same skeleton, same rules, different engine: Camoufox is an open-source
anti-detect Firefox (no licence, no keys, no seat limits — run as many as your
machine handles). Use it when you need concurrency or don't want a vendor key;
use the CloakBrowser template (mini_crawler.py) when you want the strongest
single-browser stealth on Chromium.

Verified at scale against CanLII/DataDome: a 1000-document completion run
(2026-11-11/12, 5 parallel browsers) proved three things this template now
encodes. (1) Mid-run walls cluster at ~60 docs per session — rotate the
session every ~40 docs and they mostly disappear. (2) A wall that outlasts
self-clearing means the session is burned, not the target: redraw and resume
from captures.jsonl; one worker needed 2 of 8 draws, the rest passed 1-2.
(3) Fresh-draw pass rate ~3/5, so 8 draws with a cooldown is effectively
unkillable; a supervisor relaunch on top covers crashes.

Setup (once per server):
    sudo ./cloak_setup.sh            # system deps + fonts
    pip install 'camoufox[geoip]'    # or: /opt/cloak/venv/bin/pip install ...
    camoufox fetch                   # downloads the Firefox binary (~150 MB)
    /opt/cloak/venv/bin/python -m playwright install-deps firefox

Run:  DATAIMPULSE_USER=... DATAIMPULSE_PASS=... python3 camoufox_crawler.py
      CHUNK=urls.txt OUT=data run_parallel.sh   # fan-out (see run_parallel.sh)

Note on the LeakWarning you may see at launch: with geoip=True the identity
claims another OS/GPU than the host, so Camoufox randomizes canvas and WebGL
readbacks (the browser reads as a privacy-hardened Firefox). That is intended
here and passed DataDome as-is; do not "fix" it with canvas_noise=False.
"""

import hashlib
import json
import os
import time
from pathlib import Path

# --- your crawler's knobs -----------------------------------------------------
TARGET = "https://example.com"          # page to start on
URLS = []                               # pages to fetch (or CHUNK=<url-list file>)
OUT = Path(os.environ.get("OUT", "data"))  # captures.jsonl + objects/ land here
PACE = 4.0                              # seconds between fetches — don't lower it
PROXY = {                               # sticky residential session = one IP
    "server": "http://gw.dataimpulse.com:823",
    "username": f"{os.environ.get('DATAIMPULSE_USER', 'USER')}__cr.ca;sessid.0123abcd",
    "password": os.environ.get("DATAIMPULSE_PASS", "PASS"),
}
WALL_MARKERS = ("captcha", "verify you are human", "access denied",
                "just a moment", "enable js and disable any ad blocker")
MAX_DRAWS = 8                           # session draws: planned rotations + redraws
ROTATE_EVERY = 40                       # docs per session — burnout clusters ~60
DRAW_COOLDOWN_S = 90                    # after a failed draw, before the next
# ------------------------------------------------------------------------------


def is_wall(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in WALL_MARKERS)


def wait_self_clear(page, first_url: str, tries=(8, 8, 12, 15)) -> bool:
    """Render the wall and wait for it to clear itself (~45s max).

    Same rule as on Chromium: DataDome's device check passes on its own in a
    real stealth browser on a residential IP. If it never clears, the session
    is burned — redraw it and resume; do not hammer.
    """
    page.goto(first_url, wait_until="domcontentloaded", timeout=60000)
    for waited in tries:
        time.sleep(waited)
        if not is_wall(page.content()):
            return True
    return False


def fetch_js(page, url: str) -> dict:
    """Fetch from inside the page so the request carries the session identity."""
    return page.evaluate("""async (u) => {
        const r = await fetch(u, {credentials: 'include', headers: {'Accept': 'text/html'}});
        return {status: r.status, text: await r.text()};
    }""", url)


def fetch_with_wall_retry(page, url):
    """Fetch one URL; ride out a wall once. Returns the response, or None when
    the session is burned and should be replaced."""
    res = fetch_js(page, url)
    if is_wall(res["text"]):
        if not wait_self_clear(page, url):
            return None
        time.sleep(20)  # cool-down so the rate flag decays
        res = fetch_js(page, url)
        if is_wall(res["text"]):
            return None
    return res


def store(out: Path, body: bytes, record: dict):
    digest = hashlib.sha256(body).hexdigest()
    obj = out / "objects" / digest[:2] / f"{digest}.html"
    obj.parent.mkdir(parents=True, exist_ok=True)
    obj.write_bytes(body)
    with (out / "captures.jsonl").open("a") as fh:
        fh.write(json.dumps({**record, "sha256": digest,
                             "object": str(obj.relative_to(out))}) + "\n")


def collect(page, urls, seen, out, log, limit=None):
    """Fetch until the list is done or the session should be replaced.
    Returns 'done', 'rotate', or 'wall'."""
    done = 0
    for url in urls:
        if url in seen:
            continue
        res = fetch_with_wall_retry(page, url)
        if res is None:
            print(f"[worker] wall persisted at {url} — redrawing session", flush=True)
            return "wall"
        if res["status"] == 429:
            time.sleep(30)
            res = fetch_js(page, url)
        if res["status"] == 200 and not is_wall(res["text"]):
            store(out, res["text"].encode(),
                  {"at": time.strftime("%FT%TZ"), "url": url,
                   "status": "ok", "http": res["status"]})
            seen.add(url)
            done += 1
            if done % 5 == 0:
                print(f"[worker] {done} done", flush=True)
        elif res["status"] != 200:
            print(f"[worker] {url} HTTP {res['status']} — skipped", flush=True)
        time.sleep(PACE)
        if limit and done >= limit:
            print(f"[worker] {done} docs this session — rotating session", flush=True)
            return "rotate"
    print(f"[worker] DONE {done} docs", flush=True)
    return "done"


def load_urls():
    if os.environ.get("CHUNK"):
        return [l.strip() for l in Path(os.environ["CHUNK"]).read_text().splitlines() if l.strip()]
    return list(URLS)


def main():
    out = Path(os.environ.get("OUT", "data"))
    out.mkdir(parents=True, exist_ok=True)
    urls = load_urls()
    seen = set()
    log = out / "captures.jsonl"
    if log.exists():
        for line in log.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("status") == "ok":
                seen.add(r["url"])

    from camoufox.sync_api import Camoufox

    sessid = os.environ.get("DATAIMPULSE_SESSID", "0123abcd")
    draws = 0
    need_cooldown = False
    while True:
        remaining = [u for u in urls if u not in seen]
        if not remaining:
            print(f"[worker] CHUNK COMPLETE {len(seen)} docs", flush=True)
            return
        draws += 1
        if draws > MAX_DRAWS:
            raise SystemExit(f"{MAX_DRAWS} session draws exhausted with "
                             f"{len(remaining)} URLs left — the proxy pool is hostile; stop")
        if draws > 1:
            if need_cooldown:
                print(f"[worker] cooldown {DRAW_COOLDOWN_S}s before next draw", flush=True)
                time.sleep(DRAW_COOLDOWN_S)
            sessid = os.urandom(5).hex()
            need_cooldown = False
        proxy = {
            "server": PROXY["server"],
            "username": f"{os.environ.get('DATAIMPULSE_USER', 'USER')}__cr.ca;sessid.{sessid}",
            "password": os.environ.get("DATAIMPULSE_PASS", "PASS"),
        }
        # Probe: a fresh session id is a draw from the IP lottery — a wall here
        # means redraw, not hammer. Crawl in the browser that passed the probe.
        with Camoufox(headless=True, geoip=True, humanize=True, proxy=proxy) as browser:
            page = browser.new_page()
            if not wait_self_clear(page, TARGET):
                print(f"[probe] blocked (draw {draws}) — redrawing session", flush=True)
                need_cooldown = True
                continue  # leaves the with cleanly, closing the browser
            outcome = collect(page, remaining, seen, out, log, limit=ROTATE_EVERY)
            need_cooldown = outcome == "wall"


if __name__ == "__main__":
    main()
