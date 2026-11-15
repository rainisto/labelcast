#!/bin/bash
# Change the label classification while the demo runs (labelcast picks it up within ~1 s).
# usage: setclass.sh <CLASSIFICATION>      e.g. RESTRICTED | UNCLASSIFIED | SECRET
set -euo pipefail
# shellcheck source=demo/lib.sh
source "$(dirname "$0")/lib.sh"

if [ $# -ne 1 ] || [ -z "$1" ]; then
    die "usage: $0 <CLASSIFICATION>"
fi
[[ "$1" =~ ^[A-Za-z0-9\ _-]+$ ]] || die "classification may contain letters, digits, space, _ and - only"
[ -f "$CONF" ] || die "$CONF not found; run start.sh first"
conf_edit -e "s/^classification = \".*\"/classification = \"$1\"/"
grep '^classification' "$CONF"
