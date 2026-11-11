#!/usr/bin/env python3
"""Replay crawler — POST a job list through the page that minted the session.

Why this exists: on Expedia (DataDome, 2026-11-11) every curl_cffi replay was
denied from the DataImpulse pool while the browser returned 200 on the same
sticky IP. The fix that held: mint once in a real browser, capture the API
request the page itself makes, then fire every job with an in-page fetch() —
same origin, same cookies, same TLS, same IP reputation. ~2s per request.

Copy this and change: TARGET, the capture pattern, and JOB bodies/mutator.
The pattern: probe -> let the wall self-clear -> capture the template request
-> replay each job in-page at a fixed pace -> store raw responses -> stop on 429.

Run:  python3 replay_crawler.py        (engine: Camoufox, no licence needed)
"""

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

# --- your crawler's knobs -----------------------------------------------------
TARGET = "https://www.example-hotels.com/"     # page to start on
BODIES = []                                    # JSON request bodies to POST (as str)
JOBS = []                                      # optional: {payload-diff per job}
MUTATOR = None                                 # fn(template_body_str, job) -> body_str
API_HINTS = ("graphql", "api", "search")       # capture: request URL must contain one
DROP_HEADERS = {"host", "content-length", "cookie", "origin", "referer",
                "user-agent", "accept-encoding", "connection"}
OUT = Path("data")                             # responses.jsonl + objects/ land here
PACE = 4.0                                     # seconds between posts — don't lower it
PROXY = {                                      # sticky residential session = one IP
    "server": "http://gw.dataimpulse.com:823",
    "username": "USER__cr.ca;sessid.0123abcd",  # NOTE: re-validate before reuse —
    "password": "PASS",                         # sticky pins died in <1h (see README)
}
# ------------------------------------------------------------------------------


def resolve_ip(proxy: dict) -> str:
    """Cheap pre-flight: which IP is this session pinned to right now?"""
    from urllib.parse import quote
    try:
        import httpx
        url = "http://%s:%s@%s" % (quote(proxy["username"], safe=""),
                                   quote(proxy["password"], safe=""),
                                   proxy["server"].split("://", 1)[1])
        return httpx.get("https://ipinfo.io/json", proxy=url, timeout=15).json().get("ip", "unknown")
    except Exception:
        return "unknown"


def store(out: Path, key: str, body: str, record: dict):
    digest = hashlib.sha256(body.encode()).hexdigest()
    obj = out / "objects" / digest[:2] / f"{digest}.json"
    obj.parent.mkdir(parents=True, exist_ok=True)
    obj.write_text(body)
    with (out / "responses.jsonl").open("a") as fh:
        fh.write(json.dumps({**record, "sha256": digest,
                             "object": str(obj.relative_to(out))}) + "\n")


POST_JS = """
async (args) => {
  const r = await fetch(args.url, {
    method: 'POST',
    headers: args.headers,
    body: args.body,
    credentials: 'include',
  });
  return {status: r.status, text: await r.text()};
}
"""


async def main():
    from camoufox.async_api import AsyncCamoufox

    out = OUT
    out.mkdir(exist_ok=True)
    ip = resolve_ip(PROXY)
    print(f"sticky session egress: {ip} — skip this session if you have burned it before")

    cm = AsyncCamoufox(headless=False, proxy=PROXY, geoip=False, locale="en-US", humanize=True)
    try:
        browser = await cm.__aenter__()
        page = await browser.new_page()
        await page.goto(TARGET, wait_until="domcontentloaded", timeout=60000)

        # Capture the real API request the page makes (headers included — replaying
        # with only content-type was rejected with 403 on Expedia).
        cap = {}

        def on_req(req):
            if req.method == "POST" and any(h in req.url for h in API_HINTS) and not cap.get("hit"):
                cap["hit"] = {"url": req.url, "headers": dict(req.headers), "body": req.post_data}

        page.on("request", on_req)
        await page.goto(TARGET, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(8)                      # let the page's own XHRs land
        template = cap.get("hit")
        if not template and not BODIES:
            raise SystemExit("no template request captured — loosen API_HINTS or set BODIES")

        headers = {k: v for k, v in (template or {}).get("headers", {}).items()
                   if k.lower() not in DROP_HEADERS and not k.startswith(":")}
        headers.setdefault("content-type", "application/json")
        url = template["url"] if template else TARGET

        for i, job in enumerate(JOBS or BODIES):
            body = MUTATOR(template["body"], job) if MUTATOR else job
            res = await page.evaluate(POST_JS, {"url": url, "headers": headers, "body": body})
            if res["status"] == 429:
                raise SystemExit(f"429 on job {i} — IP burned, stop; do not hammer")
            if res["status"] == 200:
                store(out, job, res["text"], {"at": time.strftime("%FT%TZ"), "job": str(job)[:80],
                                              "status": "ok", "ip": ip})
                print(f"job {i}: 200, {len(res['text'])} bytes")
            else:
                print(f"job {i}: HTTP {res['status']} — {res['text'][:80]}")
            await asyncio.sleep(PACE)
    finally:
        try:
            await cm.__aexit__(None, None, None)
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
