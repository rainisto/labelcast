#!/bin/bash
# Stop the demo: remove its containers and close its VLC window. Touches nothing else.
set -uo pipefail
# shellcheck source=demo/lib.sh
source "$(dirname "$0")/lib.sh"

"${DOCKER[@]}" rm -f "$CAM_NAME" "$GW_NAME" >/dev/null 2>&1
pattern="vlc.*udp://@:$OUT_PORT"
pkill -f "$pattern" 2>/dev/null
# Wait until VLC has really exited, so that start.sh right after this sees the port free.
for _ in $(seq 1 20); do
    pgrep -f "$pattern" >/dev/null || break
    sleep 0.25
done
pkill -9 -f "$pattern" 2>/dev/null
echo "demo stopped"
