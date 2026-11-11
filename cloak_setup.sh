#!/usr/bin/env bash
# CloakBrowser setup for Debian/Ubuntu servers — everything the CanLII crawl taught us.
# Idempotent: safe to re-run.  usage: sudo ./cloak_setup.sh
set -euo pipefail

echo "== 1. Chromium system libraries =="
# Without these the browser binary fails to launch (libatk-1.0.so.0 etc.).
python3 -m playwright install-deps chromium 2>/dev/null || {
    apt-get update -q
    apt-get install -y -q libatk1.0-0 libatk-bridge2.0-0 libcups2 libxkbcommon0 \
        libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
        libcairo2 libasound2 libnss3 libnspr4 libatspi2.0-0
}

echo "== 2. Baseline canvas/emoji/CJK fonts =="
# Anti-bots render emoji on a hidden canvas and hash the pixels. Minimal servers
# produce hashes no real browser produces — an instant tell.
apt-get install -y -q fonts-noto-color-emoji fonts-freefont-ttf fonts-unifont \
    fonts-ipafont-gothic fonts-wqy-zenhei fonts-tlwg-loma-otf

echo "== 3. Persona fonts =="
# The browser spoofs a platform ("persona"). A Windows persona with no Windows
# fonts is itself a bot tell: font-fingerprinting flags the mismatch and the
# hardest walls (DataDome) hard-block with no captcha. apt CANNOT provide
# Microsoft fonts. Two supported personas:
#
#   windows — copy from any real Windows machine:  C:\Windows\Fonts\*
#             -> /usr/local/share/fonts/windows/
#   macos   — copy from a real Mac:  /System/Library/Fonts/*
#             -> /usr/local/share/fonts/macfonts/   (about 700 MB)
#
# Then pick the matching persona at launch: --fingerprint-platform=macos|windows.
FONT_DIR=/usr/local/share/fonts/macfonts
if [ -d "$FONT_DIR" ]; then
    fc-cache -f >/dev/null 2>&1 || true
    echo "   Mac fonts present in $FONT_DIR"
else
    echo "   !! No persona fonts found in $FONT_DIR."
    echo "   !! Copy them per the comment above, then re-run fc-cache -f."
    echo "   !! Launching without them works only against easy targets."
fi

echo "== 4. Python environment =="
# Per-crawler venv; cloakbrowser bundles its own Playwright and downloads a
# patched Chromium binary (~200 MB) on first launch.
python3 -m venv /opt/cloak/venv 2>/dev/null || true
/opt/cloak/venv/bin/pip install -q --upgrade cloakbrowser
/opt/cloak/venv/bin/python -c "import cloakbrowser; print('   cloakbrowser OK')"

echo "== 5. Licence =="
# Every launch needs a key; one concurrent browser per seat. A crashed run holds
# its seat ~4 minutes — retry with backoff instead of failing.
if [ -n "${CLOAKBROWSER_LICENSE_KEY:-}" ]; then
    echo "   CLOAKBROWSER_LICENSE_KEY is set"
else
    echo "   Set CLOAKBROWSER_LICENSE_KEY (or pass license_key= at launch)."
    echo "   Free key: https://cloakbrowser.dev/free"
fi

cat <<'EOF'

== 6. Launch recipe (the rules that mattered) ==

  from cloakbrowser import launch_persistent_context_async

  ctx = await launch_persistent_context_async(
      user_data_dir="/tmp/sess-<unique>",     # fresh profile per run
      headless=True,
      geoip=False,                            # set timezone/locale yourself...
      locale="en-CA", timezone="America/Toronto",  # ...matching the proxy's country
      license_key=lic,
      proxy={                                 # STICKY residential session:
          "server": "http://gw.dataimpulse.com:823",
          "username": "USER__cr.ca;sessid.0123abcd",   # one session id = one IP
          "password": "PASS",
      },
      args=["--disable-quic", "--fingerprint-platform=macos"],  # match the fonts!
  )

  - One sticky session per run; keep the SAME session id across runs once you
    have a good IP (IP reputation is earned; fresh random sessions are a lottery).
  - Never lower the pace below what the target tolerates. Walls that appear
    despite it usually self-clear if you render them and wait ~45s.
  - Quit cleanly: a killed browser holds its licence seat ~4 minutes.

EOF
echo "setup complete"
