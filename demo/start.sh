#!/bin/bash
# Start the demo: webcam → labelcast → gateway (IReflxApp | ConfLabelReader + iptables watchdog) → VLC.
# usage: start.sh [--build]     --build builds both images first
set -euo pipefail
# shellcheck source=demo/lib.sh
source "$(dirname "$0")/lib.sh"

BUILD=0
[ "${1:-}" = "--build" ] && BUILD=1

info "checks"
"${DOCKER[@]}" version >/dev/null 2>&1 || die "'${DOCKER[*]} version' failed; set DOCKER= or check docker access"
[ -c "$CAMERA" ] || die "camera $CAMERA not found (set CAMERA=/dev/videoN; list with: ffmpeg -f v4l2 -list_formats all -i $CAMERA)"
"$DEMO_DIR/stop.sh" >/dev/null 2>&1 || true
for port in "$IN_PORT" "$OUT_PORT"; do
    if ss -lun | awk '{print $4}' | grep -q ":$port\$"; then
        die "UDP port $port is in use (set IN_PORT / OUT_PORT)"
    fi
done

if [ "$BUILD" = 1 ]; then
    info "building images (a few minutes the first time)"
    mkdir -p "$RUN_DIR"
    build() {  # build <tag> <context>
        "${DOCKER[@]}" build -t "$1" "$2" > "$RUN_DIR/build-$1.log" 2>&1 || {
            tail -20 "$RUN_DIR/build-$1.log" >&2
            die "building $1 failed (full log: $RUN_DIR/build-$1.log)"
        }
        echo "   $1 ok"
    }
    build "$LABELCAST_IMAGE" "$REPO_DIR"
    build "$READER_IMAGE" "https://github.com/rainisto/ConfLabelReader.git#iptables-2026"
fi
for image in "$LABELCAST_IMAGE" "$READER_IMAGE"; do
    "${DOCKER[@]}" image inspect "$image" >/dev/null 2>&1 || die "image $image missing; run: $0 --build"
done

info "config: $CONF"
mkdir -p "$RUN_DIR/conf"
sed -e "s#udp://127.0.0.1:[0-9]*#udp://127.0.0.1:$IN_PORT#" -e "s#/dev/video0#$CAMERA#" \
    "$DEMO_DIR/demo.conf" > "$CONF"

info "gateway (only labels matching '$REGEX' let video through)"
host_ip=$("${DOCKER[@]}" network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}')
# Never use --network host here: the watchdog's iptables commands would change the host firewall.
"${DOCKER[@]}" run -d --name "$GW_NAME" --cap-add NET_ADMIN -p "$IN_PORT:$IN_PORT/udp" "$READER_IMAGE" \
    bash -c "IReflxApp -s0.0.0.0:$IN_PORT -d$host_ip:$OUT_PORT -t1 | ConfLabelReader -r'$REGEX'" >/dev/null

info "labelcast ($CAMERA)"
run_labelcast

if [ "${NO_VLC:-0}" != 1 ]; then
    info "VLC on display $DEMO_DISPLAY"
    xauth=${XAUTHORITY:-}
    for f in /run/user/"$(id -u)"/.mutter-Xwaylandauth.*; do [ -e "$f" ] && xauth=$f; done
    DISPLAY=$DEMO_DISPLAY XAUTHORITY=$xauth XDG_RUNTIME_DIR=/run/user/$(id -u) \
        setsid nohup vlc --no-video-title-show --network-caching=300 \
        --meta-title "labelcast → ConfLabelReader gateway" "udp://@:$OUT_PORT" \
        > "$RUN_DIR/vlc.log" 2>&1 < /dev/null &
fi

for _ in $(seq 1 20); do
    [ "$(gateway_policy)" = "-P OUTPUT ACCEPT" ] && break
    sleep 0.5
done
info "gateway policy: $(gateway_policy)"
cat <<EOF

Demo running.
  $DEMO_DIR/setclass.sh RESTRICTED      # gateway closes, picture freezes within ~1-2 s
  $DEMO_DIR/setclass.sh UNCLASSIFIED    # picture resumes
  $DEMO_DIR/setoverlay.sh banner        # off | xml | banner
  $DEMO_DIR/stop.sh
EOF
