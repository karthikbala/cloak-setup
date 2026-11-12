#!/usr/bin/env python3
"""Minimal CloakBrowser crawler template — the skeleton that survived CanLII/DataDome.

Copy this and change three things: TARGET, the fetch logic, and what you store.
The pattern: probe -> let the wall self-clear -> fetch at a fixed pace ->
store bytes content-addressed -> resume from a capture log.

Learnings folded in from the 1000-document CanLII completion run
(2026-11-11/12): mid-run burnout clusters at ~60 docs per session, so the
session rotates every ROTATE_EVERY docs; a wall that outlasts self-clearing
means the session is burned, so the worker redraws and resumes from
captures.jsonl instead of dying; fresh-draw pass rate is ~3/5, so MAX_DRAWS=8
with a cooldown between draws. Same rules as the Camoufox template — only the
engine differs.

Run:  CLOAKBROWSER_LICENSE_KEY=... python3 mini_crawler.py
      CHUNK=urls.txt OUT=data run_parallel.sh   # fan-out (see run_parallel.sh)
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
    "username": "USER__cr.ca;sessid.0123abcd",
    "password": "PASS",
}
PLATFORM = "macos"                      # must match the fonts you installed
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

    Device-check walls clear on their own in a real browser on a residential
    IP. If this never clears, the session is burned: redraw it and resume —
    stop hammering the same identity.
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


def store(out: Path, url: str, body: bytes, record: dict):
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
            store(out, url, res["text"].encode(),
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

    from cloakbrowser import launch_persistent_context_sync

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
            "username": PROXY["username"].split(";sessid.")[0] + f";sessid.{sessid}",
            "password": PROXY["password"],
        }
        # Fresh profile per session: cookies are IP-bound, so a redrawn session
        # gets a fresh profile, not the old one.
        ctx = launch_persistent_context_sync(
            user_data_dir=f"/tmp/sess-{os.getpid()}-{draws}", headless=True,
            geoip=False, locale="en-CA", timezone="America/Toronto", proxy=proxy,
            args=["--disable-quic", f"--fingerprint-platform={PLATFORM}"],
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            if not wait_self_clear(page, TARGET):
                print(f"[probe] blocked (draw {draws}) — redrawing session", flush=True)
                need_cooldown = True
                continue
            outcome = collect(page, remaining, seen, out, log, limit=ROTATE_EVERY)
            need_cooldown = outcome == "wall"
        finally:
            ctx.close()  # a killed browser holds its licence seat ~4 minutes


if __name__ == "__main__":
    main()
