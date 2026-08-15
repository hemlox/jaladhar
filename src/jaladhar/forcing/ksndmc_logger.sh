#!/usr/bin/env bash
# Capture the KSNDMC live gauge feed. Deliberately dumb: no parsing, no schema,
# no transformation. Raw bytes to disk, timestamped. Parsing is Phase 1's job and
# can be redone; a 15-minute window that went unrecorded is gone forever.
#
# The endpoint is UNDOCUMENTED and may vanish without notice (OPEN-ITEMS.md 1a),
# which is the whole argument for capturing continuously starting now.
#
# Install:  crontab -l | { cat; echo "*/15 * * * * /path/to/ksndmc_logger.sh"; } | crontab -
set -uo pipefail

ROOT="${JALADHAR_ROOT:-/home/darshil/Desktop/sih/clginternal}"
OUT="$ROOT/data/raw/ksndmc/live"
LOG="$OUT/_capture.log"
UA="jaladhar-research-logger/0.1 (SIH student project; urban flood forecasting)"

# UTC for storage (unambiguous); IST is what the payload's own RAINTIME uses.
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DAY="$(date -u +%Y-%m-%d)"
mkdir -p "$OUT/$DAY"

for D in 01 02 03; do
  DEST="$OUT/$DAY/rain_d${D}_${STAMP}.json"
  # .part then mv: an interrupted fetch must never look like a complete capture.
  if curl -sS -m 45 --retry 2 --retry-delay 5 \
       -A "$UA" \
       -H 'X-Requested-With: XMLHttpRequest' \
       -H 'Referer: https://ksndmc.org/en/DailyReport/getCurrentRainFall' \
       -G "https://ksndmc.org/en/DailyReport/getCurrentRainData" \
       --data-urlencode "drpVal=$D" \
       -o "$DEST.part" 2>>"$LOG"
  then
    # Guard against a 200 that is actually the site's HTML error shell.
    if [ -s "$DEST.part" ] && head -c 1 "$DEST.part" | grep -q '["[]'; then
      SZ=$(stat -c%s "$DEST.part")
      mv "$DEST.part" "$DEST"
      # The feed is DAY-SCOPED in IST: it empties at IST midnight ("[]", 4 bytes)
      # and refills as gauges report for the new day. Empty is real information,
      # not a failure -- but log it distinctly so a genuine outage (a long empty
      # run during daytime IST) is visible instead of hiding behind "ok".
      if [ "$SZ" -le 8 ]; then
        echo "$STAMP d$D empty ${SZ}B (IST $(TZ=Asia/Kolkata date +%H:%M))" >> "$LOG"
      else
        echo "$STAMP d$D ok ${SZ}B" >> "$LOG"
      fi
    else
      mv "$DEST.part" "$DEST.rejected"
      echo "$STAMP d$D REJECTED non-json" >> "$LOG"
    fi
  else
    rm -f "$DEST.part"
    echo "$STAMP d$D FETCH_FAILED" >> "$LOG"
  fi
done
