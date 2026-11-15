#!/bin/bash
# Switch the burned-in label visualisation. The overlay is a startup option of labelcast,
# so only the labelcast container restarts (the picture pauses for a few seconds).
# usage: setoverlay.sh <off|xml|banner>
set -euo pipefail
# shellcheck source=demo/lib.sh
source "$(dirname "$0")/lib.sh"

case "${1:-}" in off|xml|banner) ;; *) die "usage: $0 <off|xml|banner>" ;; esac
[ -f "$CONF" ] || die "$CONF not found; run start.sh first"
conf_edit -e "s/^overlay = \".*\"/overlay = \"$1\"/"
grep '^overlay' "$CONF"
run_labelcast
echo "labelcast restarted"
