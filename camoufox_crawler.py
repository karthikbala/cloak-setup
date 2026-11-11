#!/usr/bin/env python3
"""Camoufox crawler template — the open-source engine twin of mini_crawler.py.

Same skeleton, same rules, different engine: Camoufox is an open-source
anti-detect Firefox (no licence, no keys, no seat limits — run as many as your
machine handles). Use it when you need concurrency or don't want a vendor key;
use the CloakBrowser template (mini_crawler.py) when you want the strongest
single-browser stealth on Chromium.

Setup (once per server):
    sudo ./cloak_setup.sh            # system deps + fonts
    pip install 'camoufox[geoip]'    # or: /opt/cloak/venv/bin/pip install ...
    camoufox fetch                   # downloads the Firefox binary (~150 MB)
    python3 -m playwright install-deps firefox

Run:  DATAIMPULSE_USER=... DATAIMPULSE_PASS=... python3 camoufox_crawler.py
"""

import hashlib
import json
import os
import time
from pathlib import Path

# --- your crawler's knobs -----------------------------------------------------
TARGET = "https://example.com"          # page to start on
URLS = []                               # the list of pages to fetch
OUT = Path("data")                      # captures.jsonl + objects/ land here
PACE = 4.0                              # seconds between fetches — don't lower it
PROXY = {                               # sticky residential session = one IP
    "server": "http://gw.dataimpulse.com:823",
    "username": f"{os.environ.get('DATAIMPULSE_USER', 'USER')}__cr.ca;sessid.0123abcd",
    "password": os.environ.get("DATAIMPULSE_PASS", "PASS"),
}
WALL_MARKERS = ("captcha", "verify you are human", "access denied",
                "just a moment", "enable js and disable any ad blocker")
# ------------------------------------------------------------------------------


def is_wall(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in WALL_MARKERS)


def wait_self_clear(page, first_url: str, tries=(8, 8, 12, 15)) -> bool:
    """Render the wall and wait for it to clear itself (~45s max).

    Same rule as on Chromium: DataDome's device check passes on its own in a
    real stealth browser on a residential IP. If it never clears, the IP or
    the fingerprint is burned — stop rather than hammer.
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


def store(out: Path, body: bytes, record: dict):
    digest = hashlib.sha256(body).hexdigest()
    obj = out / "objects" / digest[:2] / f"{digest}.html"
    obj.parent.mkdir(parents=True, exist_ok=True)
    obj.write_bytes(body)
    with (out / "captures.jsonl").open("a") as fh:
        fh.write(json.dumps({**record, "sha256": digest,
                             "object": str(obj.relative_to(out))}) + "\n")


def main():
    out = OUT
    out.mkdir(exist_ok=True)
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
    with Camoufox(headless=True, geoip=True, humanize=True, proxy=PROXY) as browser:
        page = browser.new_page()

        # Probe: a fresh session usually meets a wall here. Wait it out once.
        if not wait_self_clear(page, TARGET):
            raise SystemExit("blocked at probe — stop, do not hammer")

        for url in URLS:
            if url in seen:
                continue
            res = fetch_js(page, url)
            if is_wall(res["text"]):
                if not wait_self_clear(page, url):
                    raise SystemExit(f"wall would not clear at {url}")
                time.sleep(20)  # cool-down so the rate flag decays
                res = fetch_js(page, url)
            if res["status"] == 200 and not is_wall(res["text"]):
                store(out, res["text"].encode(), {"at": time.strftime("%FT%TZ"),
                                                  "url": url, "status": "ok"})
                seen.add(url)
            time.sleep(PACE)


if __name__ == "__main__":
    main()
