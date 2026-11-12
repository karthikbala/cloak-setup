#!/bin/sh
# Parallel fan-out with a per-worker supervisor.
#
# Each worker i gets CHUNK=<CHUNK_PREFIX>-i.jsonl (url list), OUT=<OUT_PREFIX>-i,
# its own sticky SESSID, and is relaunched once if it exits non-zero — the
# templates already redraw proxy sessions internally, so this only catches
# crashes and draw-exhaustion. Learned in the 1000-doc CanLII run: 2 of 5
# chunks needed a relaunch after mid-run burnout, and a give-up on one chunk
# would have silently dropped 58 documents.
#
# usage:  WORKER_CMD="python3 camoufox_crawler.py" CHUNK_PREFIX=urls/chunk \
#         OUT_PREFIX=data LOG_PREFIX=logs/run ./run_parallel.sh
WORKER_CMD=${WORKER_CMD:-"python3 camoufox_crawler.py"}
CHUNK_PREFIX=${CHUNK_PREFIX:-chunk}
OUT_PREFIX=${OUT_PREFIX:-data}
LOG_PREFIX=${LOG_PREFIX:-run}
WORKERS=${WORKERS:-5}
SUPERVISOR_ATTEMPTS=${SUPERVISOR_ATTEMPTS:-2}

mkdir -p "$(dirname "$LOG_PREFIX")" 2>/dev/null || true

worker() {
  i=$1
  attempt=0
  while [ "$attempt" -lt "$SUPERVISOR_ATTEMPTS" ]; do
    attempt=$((attempt+1))
    SESSID=$(dd if=/dev/urandom bs=5 count=1 2>/dev/null | od -An -tx1 | tr -d " \n")
    CHUNK=$CHUNK_PREFIX-$i.jsonl
    OUT=$OUT_PREFIX-$i
    export SESSID CHUNK OUT
    if sh -c "$WORKER_CMD" >> "$LOG_PREFIX-$i.txt" 2>&1; then
      return 0
    fi
    echo "[supervisor] worker $i failed (attempt $attempt) — relaunching in 60s" >> "$LOG_PREFIX-$i.txt"
    sleep 60
  done
}

i=0
while [ "$i" -lt "$WORKERS" ]; do
  i=$((i+1))
  worker "$i" &
done
wait
