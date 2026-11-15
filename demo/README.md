# Gateway demo: labelcast → ConfLabelReader → VLC

This demo shows that a live video stream carries a STANAG 4774 label, and
that a gateway lets the video through only while the label is acceptable.

```
webcam ─▶ labelcast ──UDP:50100──▶ gateway container ──UDP:50300──▶ VLC
          (adds the label,           IReflxApp | ConfLabelReader
           live from label.conf)     iptables watchdog: video passes only while
                                     labels match the regex (UNCLASSIFIED)
```

The label XML, or a one-line banner, is also burned into the picture so the
audience can see it. That overlay is only a visualisation. The gateway
decides on the label carried in the transport stream, not on the picture.

## Requirements

- A native Linux host. WSL2 does not work well with USB cameras.
- A USB webcam. The settings are for a Logitech C920; adjust `demo.conf`
  for other cameras.
- Docker (by default the scripts use `sudo docker`), VLC, and a desktop
  session on display `:0`.

## Running

```sh
demo/start.sh --build          # first time: builds labelcast and conflabelreader:iptables-2026
demo/start.sh                  # later runs
```

`start.sh` first checks Docker, the camera and the ports. It then copies
`demo.conf` to `~/labelcast-demo/conf/label.conf`, starts the gateway and
labelcast, and opens VLC.

Settings come from environment variables:

| Variable | Default |
|---|---|
| `CAMERA` | `/dev/video0` |
| `IN_PORT` / `OUT_PORT` | `50100` / `50300` |
| `REGEX` (labels that open the gateway) | `UNCLASSIFIED` |
| `DEMO_DISPLAY` | `:0` |
| `RUN_DIR` | `~/labelcast-demo` |
| `DOCKER` | `sudo docker` |
| `NO_VLC=1` | do not open VLC |

## Script for the audience

1. **The video arrives, and the label XML is visible top left.** The gateway
   lets it through, because the label is UNCLASSIFIED.
2. `demo/setoverlay.sh banner` switches to a large one-line banner that is
   easier to read from the back of the room. `demo/setoverlay.sh xml` goes
   back to the XML.
3. `demo/setclass.sh RESTRICTED`: the label in the picture changes at once.
   Within about a second the gateway sees that the labels no longer match,
   and the picture freezes. The frozen frame shows the label that closed the
   gateway.
4. `demo/setclass.sh UNCLASSIFIED`: the gateway opens and the picture
   resumes.
5. Optionally, edit `~/labelcast-demo/conf/label.conf` by hand, for example
   to change the classification or the releasability. A broken edit is
   rejected, and the previous label stays in force.
6. `demo/stop.sh`

You can follow what the gateway decides with
`sudo docker logs -f labelcast-demo-gateway`, and labelcast with
`sudo docker logs -f labelcast-demo-cam`.
