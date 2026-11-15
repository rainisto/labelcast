#!/bin/bash
# Stop the demo: remove its containers and close its VLC window. Touches nothing else.
set -uo pipefail
# shellcheck source=demo/lib.sh
source "$(dirname "$0")/lib.sh"

"${DOCKER[@]}" rm -f "$CAM_NAME" "$GW_NAME" >/dev/null 2>&1
pkill -f "vlc.*udp://@:$OUT_PORT" 2>/dev/null
echo "demo stopped"
