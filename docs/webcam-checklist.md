# Webcam verification checklist (native Ubuntu)

Run this on a native Linux host with a USB webcam. WSL2 is not suitable,
because USB video over usbipd is unreliable.

Prerequisites:
- `sudo apt install v4l-utils ffmpeg`
- Docker
- the images `labelcast` (`docker build -t labelcast .`) and
  `conflabelreader:iptables-2026`:
  ```sh
  docker build -t conflabelreader:iptables-2026 https://github.com/rainisto/ConfLabelReader.git#iptables-2026
  ```

Setup:
```sh
mkdir -p conf && cp examples/label.conf conf/
# in conf/label.conf set: output.url = "udp://127.0.0.1:50000?ttl=1"
```

| # | Step | Expected | Result |
|---|---|---|---|
| 1 | `v4l2-ctl --list-devices`, then `ffmpeg -f v4l2 -list_formats all -i /dev/video0` | camera listed with its formats | PASS: `/dev/video0` offers YUYV and MJPEG; `/dev/video1` is the metadata node, not usable |
| 2 | `docker run -d --name clr --cap-add NET_ADMIN -p 50000:50000/udp conflabelreader:iptables-2026 bash -c 'IReflxApp -s0.0.0.0:50000 \| ConfLabelReader -rUNCLASSIFIED'` | container running | PASS (port 50100 used); `-P OUTPUT DROP` before the stream |
| 3 | `docker run --rm --network host --device /dev/video0 -v $PWD/conf:/conf labelcast` | log `label accepted: NATO UNCLASSIFIED xml …`, stats every 10 s with labels increasing | PASS: `label accepted: NATO UNCLASSIFIED xml 664B`, 10 labels / 10 s, 0 resyncs, 0 CRC errors |
| 4 | `docker exec clr iptables-legacy -S OUTPUT` | `-P OUTPUT ACCEPT` | PASS: ACCEPT 2.0 s after labelcast started |
| 5 | Preview: point a second output at the player, or run `ffplay udp://@:50001` with an `IReflxApp -d` copy | live video visible, latency < 1 s | PARTIAL: raw UDP capture checked instead of ffplay (headless run). Capture is H.264 1280x720 at 30 fps, ~4 Mbit/s; a frame from it shows the live camera image; ConfLabelReader decoded 8 labels in ~8 s. Glass-to-glass latency not measured |
| 6 | Edit `conf/label.conf`: `classification = "RESTRICTED"` | labelcast logs `label accepted`; within ~5 s `-P OUTPUT DROP` | PASS: DROP after 3.8 s |
| 7 | Revert to `UNCLASSIFIED` | `-P OUTPUT ACCEPT` within ~2 s | PASS: ACCEPT after 1.5 s |
| 8 | Save a broken config (delete a quote) | `label change rejected: invalid TOML …`; policy unchanged | PASS: `invalid TOML: Illegal character '\n' (at line 12, column 26)`; still ACCEPT after 5 s |
| 9 | Unplug the camera | labelcast logs ffmpeg exit and restarts with backoff (1, 2, 4 … 10 s); `-P OUTPUT DROP` within ~4 s | SUBSTITUTE: ffmpeg killed inside the container (no physical unplug in a remote run): `ffmpeg exited with code 255; restarting in 1 s`, back in 1 s. The gap stayed under the 4 s watchdog, so there was correctly no DROP. Physical unplug still to do |
| 10 | Replug the camera | video and labels resume; `-P OUTPUT ACCEPT` | NOT RUN (needs physical access) |

The labelcast container can use `--network host`, because labelcast does not
touch iptables. Never run the ConfLabelReader container with
`--network host`: its iptables commands would change the host's OUTPUT
policy.

Camera model / resolution: Logitech HD Pro Webcam C920 (046d:08e5), MJPEG 1280x720@30
→ libx264 veryfast/zerolatency, 4 Mbit/s, GOP 30. Host: Ubuntu 24.04.5, kernel 6.8, Docker 29.1.3.
Run date: 2026-11-15.

ffmpeg source args used:
```toml
args = ["-f", "v4l2", "-input_format", "mjpeg", "-video_size", "1280x720", "-framerate", "30",
        "-i", "/dev/video0", "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
        "-g", "30", "-b:v", "4M"]
```

Note: to capture the output for inspection, record the raw UDP payload. Do not
use `ffmpeg -c copy`: ffmpeg's TS muxer rewrites the PMT without the 4774
descriptors, and ConfLabelReader then finds no label.
