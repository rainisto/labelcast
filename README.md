# labelcast

labelcast takes a live MPEG-TS source and binds a STANAG 4774 Confidentiality
Metadata Label to it, then sends the stream over UDP. The source can be a USB
webcam through ffmpeg, a drone or IP camera, a UDP stream or stdin. Binding
follows the STANAG 4778 / STANAG 4609 binding profile
(`urn:nato:stanag:4778:profile:4609:2.0`, see
[ConfLabelReader/BindingProfile.md](https://github.com/jimcavoy/ConfLabelReader/blob/main/BindingProfile.md)).
The label lives in a readable config file, and labelcast picks up changes to
it while the stream runs.

```
source (ffmpeg / udp / stdin) ──TS──▶ labelcast ──TS + label──▶ udp://host:port
                                          ▲
                                     label.conf (watched)
```

- Video and other streams, such as MISB 0601 KLV, pass through unchanged.
  labelcast adds a label element to the PMT and inserts label PES packets
  (`$XML` or `$BRL`) on their own PID.
- If the source already carries a 4774 label, labelcast replaces it. To keep
  the source's own label, send the source straight to ConfLabelReader instead.
- labelcast fails closed. A config change that is invalid is rejected and the
  previous label stays in use. If the source stops, nothing is sent, labels
  included.

## Configuration

See [examples/label.conf](examples/label.conf). Only the `[label]` section
can change while the stream runs. labelcast checks the file once per second,
and `SIGHUP` forces a reload.

| Key | Meaning |
|---|---|
| `source.type` | `ffmpeg` (with `args`: input and encoding options), `udp` (with `url`) or `stdin` |
| `source.overlay` | demo visualisation burned into the picture by ffmpeg: `off` (default), `xml` (the label document, top left) or `banner` (one line such as `NATO SECRET · Releasable to: FIN`, top centre). Updates live with the label. Only for an ffmpeg source that re-encodes; receivers still filter on the label PES, not on the picture |
| `output.url` | `udp://host:port[?ttl=N&iface=IP]` or `-` for stdout |
| `output.label_pid` | label PID (default `0x0102`). labelcast moves it to a free PID if this one is taken |
| `label.encoding` | `xml` or `brl` (Brotli) |
| `label.interval` | seconds between labels on the stream clock (PCR), minimum 0.1 |
| `label.policy`, `label.classification` | required in field mode |
| `label.originator` | `{ id = "...", type = "rfc822Name" }` |
| `[[label.category]]` | `tag_name`, `type` (PERMISSIVE/RESTRICTIVE/INFORMATIVE), `values` |
| `label.xml_file` | send this complete 4774 XML document instead of building one from the fields above |

## Running

```sh
python3 -m labelcast -c examples/label.conf          # needs python3-brotli, ffmpeg

docker build -t labelcast .
mkdir -p conf && cp examples/label.conf conf/      # then edit conf/label.conf
docker run --rm --network host --device /dev/video0 -v $PWD/conf:/conf labelcast
```

- The image ships no default label: without `conf/label.conf` mounted, it
  exits at startup.
- `--network host` lets `output.url = "udp://127.0.0.1:…"` reach the
  receiver on the host. Without it, the stream stays inside the container.
  Use `--network host` for labelcast only, never for the ConfLabelReader
  watchdog container, whose iptables commands would then change the host's
  firewall.
- Mount the config directory, not the single file. A bind-mounted file does
  not see changes from editors that save through a temp file and rename.
- ffmpeg is restarted when it exits or stays silent for 5 s, for example
  when the camera is unplugged or an RTSP source stops sending.

Other sources:

```toml
# drone / camera sending MPEG-TS over UDP (no re-encoding)
[source]
type = "udp"
url  = "udp://0.0.0.0:5000"

# RTSP camera, remuxed without re-encoding
[source]
type = "ffmpeg"
args = ["-rtsp_transport", "tcp", "-i", "rtsp://camera/stream", "-c", "copy"]
```

## Verifying with ConfLabelReader

Build the reader image straight from GitHub:

```sh
docker build -t conflabelreader:iptables-2026 https://github.com/rainisto/ConfLabelReader.git#iptables-2026
docker run --rm --cap-add NET_ADMIN -p 50000:50000/udp conflabelreader:iptables-2026
```

The container prints every label it receives. When it runs with
`ConfLabelReader -r'<regex>'`, its iptables watchdog allows outbound
traffic only while matching labels keep arriving. A label that does not
match blocks traffic within 250 ms. If no matching label arrives for the
watchdog timeout (`-w<ms>`, default 3000), traffic is also blocked. Keep
the timeout at least 2.5–3 times labelcast's `label.interval`.

## Tests

```sh
python3 -m pytest -m "not integration"   # unit tests
python3 -m pytest -m integration         # needs Docker and the image above (DOCKER="sudo docker" by default)
```

The sample streams in `tests/data` come from
[jimcavoy/ConfLabelReader](https://github.com/jimcavoy/ConfLabelReader) (MIT).

## License

MIT, see [LICENSE](LICENSE).
