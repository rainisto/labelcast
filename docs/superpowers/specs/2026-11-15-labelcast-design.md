# labelcast — design

Date: 2026-11-15
Status: approved in design discussion, pending written-spec review

## 1. Purpose

labelcast takes a live video source (primarily a USB webcam, later also drone
or IP-camera streams), outputs it as an MPEG-2 Transport Stream over UDP, and
binds a STANAG 4774 Confidentiality Metadata Label to the stream according to
the STANAG 4778 / STANAG 4609 binding profile
(`ConfLabelReader/BindingProfile.md`, profile `urn:nato:stanag:4778:profile:4609:2.0`).
The label is defined in a human-readable config file and can be changed while
the stream is running (e.g. raising the classification).

**Primary goal (A):** a test and demo source for the ConfLabelReader /
iptables watchdog gateway (`rainisto/ConfLabelReader`, branch `iptables-2026`).
The label carriage must be close to production quality.

**Stretch goals (B):** `$EXI` label encoding, optional XSD validation of
labels, MISB ST 0601 KLV generation, production-grade latency / video tuning.

**Success criteria**

1. ConfLabelReader decodes every label labelcast emits, for `$XML` and `$BRL`.
2. A label change in the config file is visible to ConfLabelReader within one
   label interval + 1 s, without restarting the stream.
3. In the end-to-end setup (labelcast → UDP → `IReflxApp | ConfLabelReader -r<regex>`),
   the iptables watchdog opens traffic while the classification matches and
   closes it within ~4 s after the classification is changed to a
   non-matching value, or when the source stops.
4. Video passes through unchanged; existing metadata (e.g. KLV) is preserved.

## 2. Scope

In scope (v1):

- Inputs: ffmpeg subprocess (V4L2 webcam with H.264 encoding, or RTSP/other
  sources remuxed with `-c copy`), MPEG-TS over UDP (listener), stdin.
- Single-program transport streams. If several programs are present, the
  first one in the PAT is used and a warning is logged.
- Label encodings `$XML` and `$BRL` (Brotli-compressed XML).
- Label defined by config fields or by a raw XML file.
- Live reload of the `[label]` section.
- Output: MPEG-TS over UDP (unicast or multicast, TTL), or stdout.
- Docker image for deployment on a native Linux host.

Out of scope (v1): `$EXI`, XSD validation, KLV generation, MPTS handling
beyond "first program", keeping a source's own label (to keep it, route the
source directly to ConfLabelReader instead), live change of source / output /
PID, GUI.

## 3. Architecture

```
┌────────────┐  TS  ┌──────────────────────── labelcast ───────────────────────┐
│ source     │─────▶│ TsReader → PmtRewriter → LabelInserter → output (UDP)    │──▶ udp://host:port
│ (ffmpeg /  │      │                 ▲               ▲                        │
│  udp/stdin)│      │          LabelSource ◀── ConfigWatcher ◀── label.conf    │
└────────────┘      └──────────────────────────────────────────────────────────┘
```

Every input TS packet is forwarded unchanged, except:

- PMT packets are replaced by a rebuilt PMT that includes the label element.
- Packets on any existing 4774 label PID of the source are dropped.
- Label PES packets are inserted between input packets on the label PID.

### 3.1 Modules (package `labelcast/`)

| Module | Responsibility | Depends on |
|---|---|---|
| `config.py` | Parse and validate the TOML config into immutable dataclasses (`SourceConfig`, `OutputConfig`, `LabelConfig`). Raises `ConfigError` with a clear message. | `tomllib` |
| `label.py` | Build 4774 XML from `LabelConfig` fields or load the raw XML file; validate; encode to payload bytes (`xml` / `brl`). Returns `Label(encoding, payload, summary)`. | `xml.etree`, `brotli` |
| `watcher.py` | Detect changes to the config file (and the referenced `xml_file`) by polling mtime/size once per second, plus `SIGHUP`. On change: reload → validate → build label; publish only on success. | `config`, `label` |
| `ts.py` | Pure TS logic, no I/O: packet framing / resync, PAT / PMT section assembly and parsing, PMT rebuild with CRC32/MPEG-2, label PES packetization, continuity counters, PCR extraction. | — |
| `output.py` | Group packets into 7×188-byte datagrams and send over UDP (unicast / multicast, TTL) or to stdout; flush a partial datagram after 20 ms. | `socket` |
| `source.py` | Produce a byte stream of TS: ffmpeg subprocess with restart / backoff, UDP listener, stdin. | `subprocess`, `socket` |
| `__main__.py` | CLI (`python -m labelcast -c label.conf`), logging, wiring, periodic statistics. | all |

`ts.py` and `label.py` hold the logic that must be byte-exact, and they are
pure functions / small classes so they can be tested without sockets,
processes or a camera.

## 4. Configuration

TOML file (Python 3.11+ `tomllib`).

```toml
[source]                         # read at startup only
type = "ffmpeg"                  # ffmpeg | udp | stdin
args = ["-f", "v4l2", "-i", "/dev/video0",
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency"]
# type = "udp"
# url  = "udp://0.0.0.0:5000"

[output]                         # read at startup only
url = "udp://127.0.0.1:50000?ttl=1"   # or "-" for stdout
label_pid = 0x0102

[label]                          # live-reloadable
encoding = "xml"                 # xml | brl
interval = 1.0                   # seconds between labels (stream time)
policy         = "NATO"
classification = "UNCLASSIFIED"
originator     = { id = "operator@example.org", type = "rfc822Name" }

[[label.category]]
tag_name = "Releasable to"
type     = "PERMISSIVE"          # PERMISSIVE | RESTRICTIVE | INFORMATIVE
values   = ["FIN", "SWE"]

# Instead of the fields above, a complete label document:
# xml_file = "label.xml"        # path relative to the config file
```

For an ffmpeg source, labelcast always appends `-f mpegts -` to `args` itself,
so `args` only describes the input and the encoding.

Rules:

- `[label]` either has `xml_file`, or has the fields (`policy`,
  `classification` required; `originator`, `category` optional). Having both
  is a `ConfigError`.
- Only `[label]` is applied at runtime. If `[source]` or `[output]` change
  while running, a warning says that a restart is needed, and the old values
  stay in use.
- `interval` must be > 0. Values below 0.1 s are rejected.

### 4.1 Generated XML

Field mode produces (namespace prefix `slab`, as in the upstream sample
`foreman_cif_klv_4774xml.ts`):

```xml
<?xml version="1.0" encoding="utf-8"?>
<slab:originatorConfidentialityLabel xmlns:slab="urn:nato:stanag:4774:confidentialitymetadatalabel:1:0">
  <slab:ConfidentialityInformation>
    <slab:PolicyIdentifier>NATO</slab:PolicyIdentifier>
    <slab:Classification>UNCLASSIFIED</slab:Classification>
    <slab:Category Type="PERMISSIVE" TagName="Releasable to">
      <slab:GenericValue>FIN</slab:GenericValue>
      <slab:GenericValue>SWE</slab:GenericValue>
    </slab:Category>
  </slab:ConfidentialityInformation>
  <slab:OriginatorID IDType="rfc822Name">operator@example.org</slab:OriginatorID>
  <slab:CreationDateTime>2026-11-15T12:00:00Z</slab:CreationDateTime>
</slab:originatorConfidentialityLabel>
```

- `CreationDateTime` = UTC time at which this label version was accepted.
  It does not change on each emission.
- Text and attribute values are XML-escaped.
- `xml_file` mode sends the file bytes unchanged. Validation: well-formed
  XML, and the root element is `originatorConfidentialityLabel` in namespace
  `urn:nato:stanag:4774:confidentialitymetadatalabel:1:0`.
- `brl` = Brotli compression of exactly the XML bytes above.
- Payload size limit: 65 000 bytes, so that the PES length fits into
  `PES_packet_length`.

### 4.2 Live reload

- Polling once per second (mtime + size of the config file and of
  `xml_file`), and `SIGHUP` forces a reload. Editors that save via a temp
  file + rename are handled, because the path is re-stat'ed and re-read.
- A new label is published only after a complete, successful
  parse + validate + encode. On failure the previous label stays active, and
  the error is logged with its cause. A broken label is never emitted.
- When a new label is accepted, it is emitted immediately (it does not wait
  for the next interval), and the interval timer restarts.
- If the encoding changes (`xml` ↔ `brl`), the PMT descriptors change and the
  PMT `version_number` is incremented.

## 5. Transport stream processing (`ts.py`)

### 5.1 Input framing

- 188-byte packets. If byte 0 is not 0x47, scan for the next 0x47 that is
  followed by another 0x47 188 bytes later. Count resync events.

### 5.2 PAT / PMT

- Assemble sections across packets (pointer_field, section_length). Check
  the CRC; discard a section that fails the check (counted).
- From the PAT, take the first program with `program_number != 0` → PMT PID.
- Rebuilt PMT = the source PMT with:
  - all elements removed that are 4774 labels (stream_type 0x06 or 0x15 with
    a registration_descriptor whose format_identifier is `$XML`, `$EXI` or
    `$BRL`; their PIDs go on the drop list);
  - our element appended (Table 2 of the binding profile):

    ```
    stream_type 0x06, elementary_PID = label_pid, ES_info_length 26
    05 08 <fmt:4> 34 37 37 34                       registration_descriptor
    26 0E 01 04 FF <fmt:4> 01 2F 04 34 37 37 34     Metadata_descriptor
    ```

    `<fmt>` = `$XML` or `$BRL`. For `$XML` this must be byte-identical to the
    upstream sample: `050824584d4c34373734260e0104ff24584d4c012f0434373734`,
    and for `$BRL`: `05082442524c34373734260e0104ff2442524c012f0434373734`;
  - `version_number` = (source version + local bump count) mod 32, where
    the local bump count increases on each encoding change;
  - CRC32/MPEG-2 recomputed.
- Every source PMT packet is replaced by our PMT. Our PMT is sent as many
  packets as needed (normally one), on the PMT PID, with its own continuity
  counter.
- When the source PMT version changes, our PMT is rebuilt.
- Label PID conflict: if `label_pid` is used by any element of the source
  PMT (other than a dropped label element), pick the lowest free PID ≥ 0x0102
  and log it.

### 5.3 Label PES

- Header, matching the upstream sample:
  `00 00 01 BD <PES_packet_length:2> 85 00 00`. That is stream_id 0xBD
  (private_stream_1), data_alignment_indicator = 1, original = 1, no PTS/DTS,
  PES_header_data_length = 0. Then the payload.
- `PES_packet_length` = 3 + payload length.
- Packetized on `label_pid`: the first packet has PUSI = 1. The last packet is
  padded with an adaptation field (stuffing bytes 0xFF), so no packet carries
  data from two labels. Own continuity counter per label PID.
- Labels are inserted only after at least one of our PMTs has been output.

### 5.4 Timing

- The label interval is measured in stream time from the PCR of the program's
  PCR PID (27 MHz, wraparound handled). The output rate is therefore correct
  for real-time sources and deterministic for file / stdin input
  (N seconds of stream at 1 Hz → N labels ±1).
- If no PCR has been seen for 2 s of wall-clock time, a wall-clock fallback
  is used, and switching to it is logged.
- After a PCR discontinuity (backwards jump or > 10 s gap, e.g. an ffmpeg
  restart), the interval timer is reset, and a label is emitted at the next
  insertion point.

## 6. Output

- 7 TS packets (1316 bytes) per UDP datagram. A partial datagram is flushed
  after 20 ms without new packets, and on shutdown.
- Multicast destination: set `IP_MULTICAST_TTL` from `?ttl=` (default 1).
  Optional `?iface=<ip>` sets `IP_MULTICAST_IF`.
- `url = "-"` writes the TS to stdout (for pipelines and tests).

## 7. Error handling (fail closed)

| Situation | Behaviour |
|---|---|
| Lost sync | Resync (5.1), counted in statistics |
| PSI CRC error | Section discarded, counted |
| ffmpeg exits / camera unplugged | Restart with backoff 1 s, doubling to 10 s max. PMT is re-sent, and labels resume after the PMT |
| Input silent | Nothing is output, not even labels. Labels never flow without video, and video never flows without a label. The ConfLabelReader watchdog then closes within ~4 s |
| Invalid label at startup | Exit code ≠ 0 with a clear message |
| Invalid label at reload | Keep the previous label, log the error |
| No PMT found within 5 s of input data | Warning, repeated every 10 s; packets are forwarded, no labels yet |

Logging goes to stderr: the accepted label (classification, encoding, payload
size), rejected changes with their cause, source restarts, and statistics
every 10 s (packets in/out, labels sent, resyncs, CRC errors, restarts).

## 8. Testing

1. **Unit (pytest, no camera / Docker / network):**
   - CRC32/MPEG-2 against known vectors and against the PMT in the upstream
     samples.
   - Label element bytes are equal to the `$XML` sample (5.2). The `$BRL`
     variant is compared with `foreman_cif_klv_4774brl.ts`.
   - Label PES header and packetization: lengths, PUSI, CC, stuffing.
   - PMT rebuild: existing elements are kept (H.264 0x1B, KLV 0x15), the
     existing `$EXI` label element is replaced, version_number is bumped, and
     the CRC is valid.
   - XML generation, escaping, validation rejections, raw xml_file mode,
     Brotli round trip.
   - Watcher: valid change → new label, invalid change → old label kept.
2. **Integration (Docker on WSL or Linux, no camera):**
   - `foreman_cif_klv.ts` (no label) → labelcast (stdout) → ConfLabelReader
     container: the number of labels matches the stream duration × rate, and
     the content matches the config, for `xml` and `brl`.
   - `foreman_cif_klv_4774.ts` (EXI label) → only our label in the output,
     KLV still present.
   - Config changed during a run (looped file input paced with
     `ffmpeg -re -stream_loop`) → the decoded classification changes in the
     output.
   - End-to-end with the watchdog: labelcast → UDP → `IReflxApp | ConfLabelReader -r'UNCLASSIFIED'`.
     Policy goes ACCEPT, then the classification is changed so that it no
     longer matches → DROP within ~4 s, then changed back → ACCEPT.
3. **Native Linux with a webcam (manual, scripted checklist):**
   `--device /dev/video0` → labelcast → ConfLabelReader container. Video is
   visible with `ffplay udp://…`, and classification changes drive the
   watchdog as in 2.

## 9. Packaging and environment

- Python 3.12 (Ubuntu 24.04). Runtime dependency: `brotli`
  (`python3-brotli`). Dev: `pytest`.
- Layout:
  ```
  labelcast/   config.py label.py watcher.py ts.py output.py source.py __main__.py
  tests/       unit/ integration/
  examples/    label.conf label.xml
  Dockerfile   README.md   pyproject.toml
  ```
- Docker: `ubuntu:noble` + `ffmpeg` + `python3` + `python3-brotli`.
  Run: `docker run --device /dev/video0 -v $PWD/conf:/conf labelcast -c /conf/label.conf`.
  The config directory is mounted, so it can be edited on the host while the
  stream runs. Mount the directory, not the file: a bind-mounted single file
  does not see editor rename-saves.
- Development: units 1–2 in WSL; the webcam test on a native Ubuntu host
  (Claude Code via VS Code Remote-SSH there).
- Version control: local git only until the project is complete; publishing
  to GitHub is a later, separate decision.
