# shellcheck shell=bash disable=SC2034  # variables are used by the scripts that source this file
# Shared settings and helpers for the labelcast gateway demo. Sourced by the other scripts.
# Every setting can be overridden from the environment, e.g. CAMERA=/dev/video2 ./start.sh

DEMO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=$(dirname "$DEMO_DIR")

CAMERA=${CAMERA:-/dev/video0}
IN_PORT=${IN_PORT:-50100}            # labelcast → gateway
OUT_PORT=${OUT_PORT:-50300}          # gateway → VLC on this host
REGEX=${REGEX:-UNCLASSIFIED}         # labels matching this regex open the gateway
DEMO_DISPLAY=${DEMO_DISPLAY:-:0}
RUN_DIR=${RUN_DIR:-$HOME/labelcast-demo}   # runtime copy of the config (edited live)
read -r -a DOCKER <<< "${DOCKER:-sudo docker}"

LABELCAST_IMAGE=labelcast
READER_IMAGE=conflabelreader:iptables-2026
CAM_NAME=labelcast-demo-cam
GW_NAME=labelcast-demo-gateway
CONF="$RUN_DIR/conf/label.conf"

die() { echo "error: $*" >&2; exit 1; }
info() { echo "== $*"; }

# Replace the config atomically, like an editor does, so labelcast never reads half a file.
conf_edit() {  # conf_edit <sed expression>...
    local tmp="$CONF.tmp"
    sed "$@" "$CONF" > "$tmp" && mv "$tmp" "$CONF"
}

run_labelcast() {
    "${DOCKER[@]}" rm -f "$CAM_NAME" >/dev/null 2>&1 || true
    # Host network: labelcast sends to 127.0.0.1:$IN_PORT, published by the gateway container.
    "${DOCKER[@]}" run -d --name "$CAM_NAME" --network host --device "$CAMERA" \
        -v "$RUN_DIR/conf:/conf" "$LABELCAST_IMAGE" >/dev/null
}

gateway_policy() {
    "${DOCKER[@]}" exec "$GW_NAME" iptables-legacy -S OUTPUT 2>/dev/null
}
