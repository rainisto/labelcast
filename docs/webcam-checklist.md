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
| 1 | `v4l2-ctl --list-devices`, then `ffmpeg -f v4l2 -list_formats all -i /dev/video0` | camera listed with its formats | not run yet |
| 2 | `docker run -d --name clr --cap-add NET_ADMIN -p 50000:50000/udp conflabelreader:iptables-2026 bash -c 'IReflxApp -s0.0.0.0:50000 \| ConfLabelReader -rUNCLASSIFIED'` | container running | not run yet |
| 3 | `docker run --rm --network host --device /dev/video0 -v $PWD/conf:/conf labelcast` | log `label accepted: NATO UNCLASSIFIED xml …`, stats every 10 s with labels increasing | not run yet |
| 4 | `docker exec clr iptables-legacy -S OUTPUT` | `-P OUTPUT ACCEPT` | not run yet |
| 5 | Preview: point a second output at the player, or run `ffplay udp://@:50001` with an `IReflxApp -d` copy | live video visible, latency < 1 s | not run yet |
| 6 | Edit `conf/label.conf`: `classification = "RESTRICTED"` | labelcast logs `label accepted`; within ~5 s `-P OUTPUT DROP` | not run yet |
| 7 | Revert to `UNCLASSIFIED` | `-P OUTPUT ACCEPT` within ~2 s | not run yet |
| 8 | Save a broken config (delete a quote) | `label change rejected: invalid TOML …`; policy unchanged | not run yet |
| 9 | Unplug the camera | labelcast logs ffmpeg exit and restarts with backoff (1, 2, 4 … 10 s); `-P OUTPUT DROP` within ~4 s | not run yet |
| 10 | Replug the camera | video and labels resume; `-P OUTPUT ACCEPT` | not run yet |

The labelcast container can use `--network host`, because labelcast does not
touch iptables. Never run the ConfLabelReader container with
`--network host`: its iptables commands would change the host's OUTPUT
policy.

Camera model / resolution: _(fill in)_
