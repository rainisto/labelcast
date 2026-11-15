# labelcast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python tool that reads a live MPEG-TS source (ffmpeg webcam/RTSP, UDP or stdin), binds a live-reloadable STANAG 4774 label to it according to the 4609 binding profile, and sends the stream over UDP.

**Architecture:** Single process, single-threaded main loop: source → `PacketFramer` → `Injector` (PMT rewrite, label PES insertion, PCR timing) → output. `LabelWatcher` is polled from the same loop. Byte-level TS logic lives in pure functions in `ts.py`; the stateful per-stream logic is in `inject.py`. This is a refinement of spec §3.1: the "PmtRewriter / LabelInserter" responsibilities move from `ts.py` into `inject.py`, so that `ts.py` stays free of state.

**Tech Stack:** Python 3.12 stdlib (`tomllib`, `xml.etree`, `socket`, `selectors`, `subprocess`), `brotli`, `pytest`; ffmpeg; Docker for integration tests against `conflabelreader:iptables-2026`.

**Spec:** `docs/superpowers/specs/2026-11-15-labelcast-design.md`

## Global Constraints

- Python ≥ 3.12. The only runtime dependency is `brotli`. Dev dependency: `pytest`.
- Label element bytes `$XML`: `050824584d4c34373734260e0104ff24584d4c012f0434373734`; `$BRL`: `05082442524c34373734260e0104ff2442524c012f0434373734`; stream_type `0x06`.
- Label PES header: `00 00 01 BD <len:2> 85 00 00`, where `len` = 3 + payload length; payload ≤ 65000 bytes.
- 4774 namespace `urn:nato:stanag:4774:confidentialitymetadatalabel:1:0`, prefix `slab`, root `originatorConfidentialityLabel`.
- Default `label_pid` is 0x0102. On conflict, use the lowest free PID ≥ 0x0102.
- `interval` must be ≥ 0.1 s. It is measured on the PCR clock (27 MHz), with a wall-clock fallback after 2 s without PCR. A PCR gap > 10 s or a backwards jump counts as a discontinuity.
- UDP output: 7 packets (1316 B) per datagram, partial flush after 20 ms; multicast `?ttl=` (default 1), `?iface=`.
- ffmpeg restart backoff 1 s, doubling to 10 s max; the backoff resets after 10 s of continuous data.
- Watcher polling once per second, plus `SIGHUP`. Stats every 10 s. "No PMT" warning 5 s after the first data, repeated every 10 s.
- Logging goes to stderr only (stdout may carry TS).
- All commands run in WSL in `/home/jonni/labelcast`. Docker needs `sudo`; tests read the `DOCKER` env var (default `sudo docker`).
- Commit trailer on every commit: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Input arrives in chunks that are not 188-aligned, or contains garbage (UDP datagram boundaries, a broken ffmpeg start). Expected: the framer resyncs, and no packet is lost or invented. → Task 1, `test_framer_*`.
2. A PMT section spans two packets, or a packet holds the PMT plus 0xFF stuffing. Expected: it is parsed correctly, and the CRC is validated. → Task 1, `test_section_assembler_*`.
3. The config is saved half-written (partial TOML, or XML with `&`/`<` in the classification). Expected: a partial save is rejected and the old label kept; special characters are escaped, never broken. → Task 3 and Task 4.
4. PCR wraparound or a backwards jump after an ffmpeg restart. Expected: labels keep flowing, and one is emitted at the next opportunity. → Task 5, `test_pcr_discontinuity_*`.
5. A source that already uses PID 0x0102, or carries an EXI label. Expected: our label moves to a free PID, and the source label is removed. → Task 5.

---

### Task 0: Project scaffold and dev environment

**Files:**
- Create: `pyproject.toml`, `labelcast/__init__.py`, `tests/__init__.py`, `tests/conftest.py`, `tests/data/` (4 samples + `NOTICE`), `.gitignore`

- [ ] **Step 1: Install system packages**

Run: `sudo apt-get install -y python3-brotli python3-pytest ffmpeg`
Expected: `python3 -c 'import brotli'` and `python3 -m pytest --version` succeed, and `ffmpeg -version` prints a version.

- [ ] **Step 2: Copy samples**

Copy `foreman_cif_klv.ts`, `foreman_cif_klv_4774.ts`, `foreman_cif_klv_4774xml.ts` and `foreman_cif_klv_4774brl.ts` from https://github.com/jimcavoy/ConfLabelReader/tree/main/Sample into `tests/data/`. Add `tests/data/NOTICE` naming the source repository, its MIT license and the copyright line `Copyright (c) 2021 James McAvoy`.

- [ ] **Step 3: Write `pyproject.toml` and `conftest.py`**

- `pyproject.toml`: project `labelcast`, `requires-python = ">=3.12"`, dependency `brotli`, pytest `markers = ["integration: needs docker image conflabelreader:iptables-2026"]`, `testpaths = ["tests"]`.
- `conftest.py`: fixture `data_dir() -> Path` (= `tests/data`), and fixture `docker_cmd() -> list[str]`, which is `os.environ.get("DOCKER", "sudo docker").split()`. Integration tests are skipped when `docker image inspect conflabelreader:iptables-2026` fails.

- [ ] **Step 4: Verify**

Run: `python3 -m pytest`
Expected: `no tests ran`, exit code 5.

- [ ] **Step 5: Commit** (`chore: scaffold labelcast project with sample data`)

---

### Task 1: TS primitives — CRC, framing, PSI sections

**Files:**
- Create: `labelcast/ts.py`
- Test: `tests/unit/test_ts_basic.py`

**Interfaces:**
- Produces (in `labelcast/ts.py`):
  - `PACKET_SIZE = 188`
  - `crc32_mpeg2(data: bytes) -> int`
  - `class PacketFramer: feed(data: bytes) -> list[bytes]`, attribute `resyncs: int`
  - `pid(pkt: bytes) -> int`, `pusi(pkt: bytes) -> bool`, `payload(pkt: bytes) -> bytes` (empty if there is no payload), `pcr(pkt: bytes) -> int | None` (27 MHz units = base*300 + ext)
  - `class SectionAssembler: push(pkt: bytes) -> list[bytes]` returns complete sections (table_id through CRC) whose CRC is valid; attribute `crc_errors: int`. One assembler per PID.
  - `parse_pat(section: bytes) -> list[tuple[int, int]]` returns `(program_number, pmt_pid)`, excluding program 0

- [ ] **Step 1: Write failing tests**

```python
def test_crc32_mpeg2_vector():
    assert crc32_mpeg2(b"123456789") == 0x0376E6E7

def test_framer_split_chunks(data_dir):
    raw = (data_dir / "foreman_cif_klv.ts").read_bytes()
    f = PacketFramer()
    out = [p for i in range(0, len(raw), 1000) for p in f.feed(raw[i:i + 1000])]
    assert b"".join(out) == raw and f.resyncs == 0

def test_framer_resyncs_after_garbage(data_dir):
    raw = (data_dir / "foreman_cif_klv.ts").read_bytes()[:188 * 10]
    f = PacketFramer()
    out = f.feed(raw[:188 * 3] + b"\x00\x47\x13" + raw[188 * 3:])
    assert out == [raw[i:i + 188] for i in range(0, len(raw), 188)] and f.resyncs == 1

def test_section_assembler_pat_and_pmt(data_dir):
    # PAT → PMT PID 0x1000 (program 1); PMT section from the sample has a valid CRC
    ...  # assert parse_pat(pat) == [(1, 0x1000)]; crc32_mpeg2(pmt) == 0

def test_section_assembler_spanning_two_packets():
    # build a 300-byte section with a valid CRC, split it over 2 packets (PUSI only on the first)
    ...  # assert push(p1) == [] and push(p2) == [section]

def test_section_assembler_rejects_bad_crc():
    ...  # one flipped byte → [] and crc_errors == 1

def test_pcr_extraction(data_dir):
    # first packet on PID 0x100 that has a PCR → int > 0; packets without an adaptation field → None
```

- [ ] **Step 2: Run** `python3 -m pytest tests/unit/test_ts_basic.py -v`. Expected: FAIL (ImportError).

- [ ] **Step 3: Implement** the functions above.
  - CRC: polynomial 0x04C11DB7, init 0xFFFFFFFF, no reflection, no final XOR; 256-entry table.
  - Framer: keep the remainder between calls. When the buffer does not start with 0x47, drop bytes until a 0x47 is found that is followed by a 0x47 188 bytes later (or the buffer ends), and count one resync per such drop.
  - SectionAssembler: a PUSI packet starts a new section after `pointer_field`; length = 3 + `section_length`; bytes after a complete section are ignored if they are 0xFF.

- [ ] **Step 4: Run** the same tests. Expected: PASS.

- [ ] **Step 5: Commit** (`feat(ts): CRC32, packet framing and PSI section assembly`)

---

### Task 2: TS PMT model and label element, PES packetization

**Files:**
- Modify: `labelcast/ts.py`
- Test: `tests/unit/test_ts_pmt_pes.py`

**Interfaces:**
- Consumes: Task 1 functions.
- Produces:
  - `@dataclass class PmtElement: stream_type: int; pid: int; es_info: bytes`
  - `@dataclass class Pmt: program_number: int; version: int; pcr_pid: int; program_info: bytes; elements: list[PmtElement]`
  - `parse_pmt(section: bytes) -> Pmt`, `build_pmt_section(pmt: Pmt) -> bytes` (table_id 0x02, section_syntax 1, current_next 1, section 0/0, CRC appended)
  - `label_element(pid: int, fmt: bytes) -> PmtElement` (`fmt` is `b"$XML"` or `b"$BRL"`)
  - `is_label_element(el: PmtElement) -> bool`: stream_type is 0x06 or 0x15, and the registration descriptor (tag 0x05) format_identifier is in `{$XML, $EXI, $BRL}`
  - `class ContinuityCounter: next() -> int` (0..15 wrap)
  - `packetize_section(pid: int, section: bytes, cc: ContinuityCounter) -> list[bytes]` (pointer_field 0, 0xFF fill)
  - `build_label_pes(payload: bytes) -> bytes`
  - `packetize_pes(pid: int, pes: bytes, cc: ContinuityCounter) -> list[bytes]` (PUSI on the first packet; adaptation-field stuffing 0xFF in the last packet)

- [ ] **Step 1: Write failing tests**

```python
XML_EL = bytes.fromhex("050824584d4c34373734260e0104ff24584d4c012f0434373734")
BRL_EL = bytes.fromhex("05082442524c34373734260e0104ff2442524c012f0434373734")

def test_label_element_bytes_match_samples():
    assert label_element(0x102, b"$XML") == PmtElement(0x06, 0x102, XML_EL)
    assert label_element(0x102, b"$BRL").es_info == BRL_EL

def test_parse_build_roundtrip(xml_sample_pmt_section):
    assert build_pmt_section(parse_pmt(xml_sample_pmt_section)) == xml_sample_pmt_section

def test_is_label_element(exi_sample_pmt):   # elements: 0x1B video, 0x15 KLVA, 0x06 $EXI
    assert [is_label_element(e) for e in exi_sample_pmt.elements] == [False, False, True]

def test_label_pes_header():
    pes = build_label_pes(b"x" * 705)
    assert pes[:9] == bytes.fromhex("000001bd02c4850000") and len(pes) == 9 + 705

def test_packetize_pes_lengths_and_stuffing():
    cc = ContinuityCounter()
    pkts = packetize_pes(0x102, build_label_pes(b"y" * 400), cc)
    assert all(len(p) == 188 for p in pkts) and pusi(pkts[0]) and not any(pusi(p) for p in pkts[1:])
    assert b"".join(payload(p) for p in pkts) == build_label_pes(b"y" * 400)
    assert [p[3] & 0x0F for p in pkts] == list(range(len(pkts)))

def test_packetize_pes_exact_multiple():   # PES of exactly 184*2 bytes → 2 packets, no adaptation field
    ...
```
The fixtures `xml_sample_pmt_section` and `exi_sample_pmt` go into `tests/conftest.py`, built with Task 1 (the first complete PMT section on PID 0x1000).

- [ ] **Step 2: Run** `python3 -m pytest tests/unit/test_ts_pmt_pes.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above. `build_label_pes` raises `ValueError` if the payload is > 65000 bytes.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** (`feat(ts): PMT model, 4774 label element and PES packetization`)

---

### Task 3: Config parsing

**Files:**
- Create: `labelcast/config.py`, `examples/label.conf`, `examples/label.xml`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Produces (frozen dataclasses):
  - `SourceConfig(type: str, args: tuple[str, ...], url: str | None)`; `type` is one of `ffmpeg|udp|stdin`
  - `OutputConfig(url: str, label_pid: int)`
  - `Originator(id: str, type: str)`, `Category(tag_name: str, type: str, values: tuple[str, ...])`
  - `LabelConfig(encoding: str, interval: float, policy: str | None, classification: str | None, originator: Originator | None, categories: tuple[Category, ...], xml_file: Path | None)`; `xml_file` is resolved relative to the config file
  - `Config(source: SourceConfig, output: OutputConfig, label: LabelConfig)`
  - `class ConfigError(Exception)`
  - `parse_config(text: str, base_dir: Path) -> Config`, `load_config(path: Path) -> Config`
- Defaults: `encoding="xml"`, `interval=1.0`, `label_pid=0x0102`.

- [ ] **Step 1: Write failing tests**

```python
def test_example_config_parses():           # examples/label.conf = spec §4 example
    cfg = load_config(Path("examples/label.conf"))
    assert cfg.label.classification == "UNCLASSIFIED"
    assert cfg.label.categories[0].values == ("FIN", "SWE")
    assert cfg.output.label_pid == 0x0102 and cfg.source.type == "ffmpeg"

@pytest.mark.parametrize("snippet, msg", [
    ('[label]\nxml_file="a.xml"\npolicy="NATO"\nclassification="X"', "xml_file"),   # both modes
    ('[label]\npolicy="NATO"', "classification"),
    ('[label]\npolicy="NATO"\nclassification="X"\ninterval=0.05', "interval"),
    ('[label]\npolicy="NATO"\nclassification="X"\nencoding="exi"', "encoding"),
    ('[label]\npolicy="NATO"\nclassification="X"\n[[label.category]]\ntag_name="t"\ntype="BOGUS"\nvalues=["A"]', "type"),
    ('[label]\npolicy="NATO"\nclassification="X', "TOML"),                            # half-written file
])
def test_invalid_configs(snippet, msg):
    with pytest.raises(ConfigError, match=msg): parse_config(BASE + snippet, Path("."))
```
`BASE` is a valid `[source]` + `[output]` prefix. `examples/label.xml` is the spec §4.1 document.

- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement.** Wrap `tomllib.TOMLDecodeError` as `ConfigError("invalid TOML: …")`. Every error message names the offending key.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** (`feat(config): TOML config model and validation`)

---

### Task 4: Label building and encoding

**Files:**
- Create: `labelcast/label.py`
- Test: `tests/unit/test_label.py`

**Interfaces:**
- Consumes: `LabelConfig`, `Category`, `Originator` (Task 3).
- Produces:
  - `@dataclass(frozen=True) class Label: encoding: str; fmt: bytes; payload: bytes; summary: str`. Here `fmt` is `b"$XML"` or `b"$BRL"`; `summary` reads e.g. `"NATO UNCLASSIFIED xml 612B"`, or `"file label.xml brl 280B"` in xml_file mode.
  - `class LabelError(Exception)`
  - `build_label_xml(cfg: LabelConfig, created: datetime) -> bytes`
  - `validate_label_xml(xml: bytes) -> None` (raises `LabelError`)
  - `make_label(cfg: LabelConfig, now: datetime) -> Label`

- [ ] **Step 1: Write failing tests**

```python
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)

def test_build_matches_spec_example():
    assert build_label_xml(EXAMPLE_CFG.label, NOW) == Path("examples/label.xml").read_bytes()

def test_escaping():
    xml = build_label_xml(replace(EXAMPLE_CFG.label, classification='A&B<"C"'), NOW)
    validate_label_xml(xml)
    assert b"A&amp;B&lt;" in xml

def test_xml_file_mode_sends_bytes_unchanged(tmp_path): ...
def test_xml_file_wrong_root_rejected(tmp_path): ...            # LabelError match "originatorConfidentialityLabel"
def test_xml_file_malformed_rejected(tmp_path): ...             # LabelError match "well-formed"
def test_xml_file_missing_rejected(tmp_path): ...               # LabelError
def test_brl_roundtrip():
    lab = make_label(replace(EXAMPLE_CFG.label, encoding="brl"), NOW)
    assert lab.fmt == b"$BRL" and brotli.decompress(lab.payload) == build_label_xml(EXAMPLE_CFG.label, NOW)

def test_payload_too_large_rejected(tmp_path): ...              # > 65000 B → LabelError
```

- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Build the XML as text with 2-space indentation, `\n` line endings and attribute order `Type`, `TagName` (exactly as in spec §4.1), using `xml.sax.saxutils.escape`/`quoteattr` for escaping. The goal is byte-stable output rather than an ElementTree serializer.
  - `CreationDateTime` is formatted as `%Y-%m-%dT%H:%M:%SZ`.
  - Validation parses with `xml.etree.ElementTree.fromstring` and checks the root tag `{urn:nato:stanag:4774:confidentialitymetadatalabel:1:0}originatorConfidentialityLabel`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** (`feat(label): 4774 XML generation, validation, xml/brl encoding`)

---

### Task 5: Injector — PMT rewrite, label insertion, timing

**Files:**
- Create: `labelcast/inject.py`
- Test: `tests/unit/test_inject.py`

**Interfaces:**
- Consumes: Tasks 1, 2 and 4 (`Label`).
- Produces: `class Injector(label_pid: int = 0x0102, clock: Callable[[], float] = time.monotonic, log: logging.Logger | None = None)`
  - `set_label(label: Label, interval: float) -> None`: the new label is emitted at the next insertion point.
  - `process(pkt: bytes) -> list[bytes]`: the output packets for one input packet.
  - `reset() -> None`: forget the PAT/PMT state (called after a source restart; labels pause until the next PMT).
  - `pmt_seen: bool`, `active_label_pid: int | None`, `stats: dict[str, int]` (keys `packets_in`, `packets_out`, `labels`, `pmt_versions`, `dropped`).

Behaviour (spec §5):
- PAT is passed through. It sets the PMT PID from the first program; with more than one program, log a warning once.
- Every source PMT packet is dropped. Each time a source PMT section completes, our packetized PMT is output instead, built as follows:
  - drop the label elements and add their PIDs to the drop set;
  - choose the label PID (configured, or the lowest free PID ≥ 0x0102);
  - append `label_element`;
  - set version = (source version + bumps) % 32, where bumps counts fmt changes after the first label.
- Packets on dropped PIDs are discarded.
- Timing:
  - Once our PMT has been output and a label is set, a label is due when the last-label time is unset, or a new label is pending, or the PCR on the program's PCR PID has advanced ≥ `interval × 27e6` since the last label (modulo 2^33·300).
  - A modulo difference > 10 s × 27e6 counts as a discontinuity: the label is due now.
  - If no PCR has been seen for 2 s on `clock()`, the same rule runs on `clock()` seconds instead (log the switch once).
  - Due labels are appended after the current packet.

- [ ] **Step 1: Write failing tests.** These helpers go in `tests/helpers.py`, which Tasks 8 and 9 reuse:
  - `run(inj, path) -> bytes` feeds a sample file through `PacketFramer` + `process`.
  - `first_pmt(ts: bytes) -> Pmt`
  - `labels_in(ts: bytes) -> list[tuple[int, bytes]]` returns `(pid, payload)` for each label PES on the PID that the PMT declares.
  - `stream_seconds(ts: bytes) -> float` = (last − first PCR on the PCR PID) / 27e6.

```python
def test_adds_label_to_unlabelled_sample(data_dir):
    inj = Injector(); inj.set_label(XML_LABEL, 1.0)
    out = run(inj, data_dir / "foreman_cif_klv.ts")
    pmt = first_pmt(out)
    assert [e.stream_type for e in pmt.elements] == [0x1B, 0x15, 0x06]
    assert pmt.elements[2].es_info == XML_EL
    labels = labels_in(out)
    assert all(p == XML_LABEL.payload for _, p in labels)
    assert abs(len(labels) - round(stream_seconds(out))) <= 1

def test_replaces_exi_label(data_dir):           # only our $XML element and payloads; KLV PID 0x101 still present
def test_pid_conflict_moves_label(data_dir):     # foreman_cif_klv_4774.ts, Injector(label_pid=0x101) → active_label_pid == 0x102 (0x101 is KLV; 0x102 freed by the dropped EXI label → allowed)
def test_label_change_bumps_version_on_fmt_change(data_dir):  # xml → brl mid-file: next PMT version == first + 1, es_info == BRL_EL, next PES payload == BRL payload
def test_new_label_emitted_immediately(data_dir) # set_label mid-stream: next label PES comes within 2 input packets
def test_no_labels_before_pmt():                 # feed only PAT + video packets → no label PID packets
def test_pcr_discontinuity_backwards(data_dir):  # feed file twice (PCR jumps back) → a label within 2 packets after the jump
def test_pcr_wraparound():                       # synthetic PCRs near 2^33*300 → interval still respected
def test_wallclock_fallback():                   # stream without PCR + fake clock advancing 0.5 s/packet → labels at ~1 s
def test_reset_pauses_labels(data_dir)           # after reset(), no label until the next PMT
def test_continuity_counters_valid(data_dir)     # CC on PMT and label PIDs increments by 1 mod 16
```

- [ ] **Step 2: Run** `python3 -m pytest tests/unit/test_inject.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `Injector` as described.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** (`feat(inject): PMT rewrite and PCR-timed label insertion`)

---

### Task 6: Watcher

**Files:**
- Create: `labelcast/watcher.py`
- Test: `tests/unit/test_watcher.py`

**Interfaces:**
- Consumes: `load_config`, `ConfigError` (Task 3); `make_label`, `LabelError`, `Label` (Task 4).
- Produces: `class LabelWatcher(path: Path, config: Config, log: logging.Logger, now: Callable[[], datetime] = utcnow)`
  - `label: Label` (current), `interval: float` (current)
  - `check() -> bool`: stat the config and `xml_file`. If (mtime_ns, size) changed or a reload was requested, reload. Returns True only when a new label was accepted.
  - `request_reload() -> None` (for SIGHUP)
  - The constructor builds the initial label and propagates `LabelError` (startup must fail).

- [ ] **Step 1: Write failing tests**

```python
def test_valid_change_accepted(tmp_path):        # classification change → check() True, label.summary contains new value
def test_invalid_change_keeps_old(tmp_path, caplog):  # write half TOML → check() False, label unchanged, log mentions "TOML"
def test_recovers_after_fix(tmp_path):            # invalid then valid → True
def test_source_change_warns_restart(tmp_path, caplog):  # [output] url changed → label unaffected, warning contains "restart"
def test_xml_file_change_detected(tmp_path):      # edit only label.xml → True
def test_rename_save_detected(tmp_path):          # write tmp + os.replace → True
def test_request_reload_forces(tmp_path, caplog)  # no change on disk, fake now advanced 1 s, request_reload() → check() True and log contains "label accepted"
```

- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement.** On a successful reload, log at INFO: `label accepted: <summary>`. On failure, log at ERROR: `label change rejected: <reason>`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** (`feat(watcher): live label reload with validation`)

---

### Task 7: Output and sources

**Files:**
- Create: `labelcast/output.py`, `labelcast/source.py`
- Test: `tests/unit/test_output.py`, `tests/unit/test_source.py`

**Interfaces:**
- Produces:
  - `output.py`:
    - `open_output(url: str) -> Output`, where `"-"` → `StdoutOutput` and `udp://host:port[?ttl=N&iface=IP]` → `UdpOutput`
    - `Output` protocol: `write(pkts: list[bytes]) -> None`, `tick(now: float) -> None` (flush a partial datagram ≥ 20 ms old), `close() -> None`
  - `source.py`:
    - `open_source(cfg: SourceConfig, log) -> Source`
    - `Source` protocol: `read(timeout: float) -> bytes` (`b""` on timeout; raises `EOFError` at the end of stdin), `restarts: int`, `close()`
    - `FfmpegSource` runs `["ffmpeg", "-hide_banner", "-loglevel", "warning", *args, "-f", "mpegts", "-"]` and applies the restart/backoff from Global Constraints.
    - `UdpSource` binds the host and port of `url` and joins the group if it is multicast.
    - `StdinSource` reads `sys.stdin.buffer`.

- [ ] **Step 1: Write failing tests**

```python
def test_udp_output_datagram_sizes():   # local UDP receiver; write 15 packets → datagrams [1316, 1316]; tick(+0.03) → [188]
def test_udp_output_query_parsing():    # "udp://239.1.1.1:5000?ttl=3" → IP_MULTICAST_TTL == 3
def test_stdout_output(capfdbinary)     # bytes written unchanged
def test_udp_source_receives(): ...     # send 2 datagrams → read() returns their concatenation
def test_ffmpeg_source_restarts(monkeypatch):  # fake ffmpeg = ["python3","-c","import sys; sys.stdout.buffer.write(b'G'*188)"] → after exit, restarts == 1 after ≥1 s, then 2 after ≥2 s more
```
In `FfmpegSource`, the command prefix is overridable through the constructor argument `cmd: list[str] = ["ffmpeg", ...]`, for the test.

- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement.** Use `selectors` for timeouts. ffmpeg stderr is forwarded to our log at WARNING level.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** (`feat(io): UDP/stdout output and ffmpeg/udp/stdin sources`)

---

### Task 8: CLI main loop, Docker, README

**Files:**
- Create: `labelcast/__main__.py`, `Dockerfile`, `README.md`
- Test: `tests/unit/test_main.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `main(argv: list[str] | None = None) -> int`. Options: `-c/--config PATH` (required), `-v/--verbose`.
  - Loop: `source.read(0.02)` → framer → injector → output; `output.tick`.
  - `watcher.check()` once per second, then `injector.set_label(watcher.label, watcher.interval)` when it returns True.
  - SIGHUP → `watcher.request_reload()`.
  - `source.restarts` increased → `injector.reset()`.
  - Stats every 10 s; "no PMT" warning per Global Constraints.
  - Exit codes: 0 on EOF or SIGINT/SIGTERM, 2 on config or label error at startup.

- [ ] **Step 1: Write failing tests**

```python
def test_main_stdin_to_stdout(data_dir, tmp_path):   # subprocess: python3 -m labelcast -c conf (stdin→"-") < foreman_cif_klv.ts
    # exit 0; output has the label element in the PMT; label count within ±1 of stream seconds
def test_main_bad_config_exit_2(tmp_path):            # classification missing → exit 2, stderr contains "classification"
```

- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement `main`.** Also write:
  - `Dockerfile`: `ubuntu:noble` + `ffmpeg python3 python3-brotli`; copy the package; `ENTRYPOINT ["python3","-m","labelcast"]`, `CMD ["-c","/conf/label.conf"]`.
  - `README.md`: purpose, config reference (spec §4), run examples (webcam: `docker run --device /dev/video0 -v $PWD/conf:/conf labelcast`; mount the directory, not the file), and how to verify with ConfLabelReader.
- [ ] **Step 4: Run** `python3 -m pytest -m "not integration"`. Expected: all PASS. Then run `sudo docker build -t labelcast .`. Expected: success.
- [ ] **Step 5: Commit** (`feat: CLI main loop, Dockerfile and README`)

---

### Task 9: Integration tests against ConfLabelReader

**Files:**
- Create: `tests/integration/test_conflabelreader.py`

**Interfaces:**
- Consumes: the `labelcast` CLI (Task 8) and the docker image `conflabelreader:iptables-2026` (built from `https://github.com/rainisto/ConfLabelReader.git#iptables-2026`).

- [ ] **Step 1: Write the tests** (all `@pytest.mark.integration`; helper `decode(ts: bytes) -> str` = stdout of `docker run --rm -i conflabelreader:iptables-2026 ConfLabelReader`, with the TS as stdin)

```python
@pytest.mark.parametrize("enc", ["xml", "brl"])
def test_reader_decodes_our_labels(enc, data_dir, tmp_path):
    # labelcast stdin→stdout over foreman_cif_klv.ts; decode() output contains "Labels read: N"
    # with |N - stream_seconds| <= 1 and "<slab:Classification>UNCLASSIFIED</slab:Classification>"

def test_reader_sees_only_our_label_on_exi_source(data_dir, tmp_path):
    # foreman_cif_klv_4774.ts in; decoded labels all contain our OriginatorID, none "jean.guy@forces.gc.ca"

def test_live_reload_visible_in_reader(data_dir, tmp_path):
    # source ffmpeg args ["-re","-stream_loop","-1","-i",<sample>,"-c","copy"], output udp://127.0.0.1:<free port>
    # container: IReflxApp -s0.0.0.0:<port> | ConfLabelReader, with -p <port>:<port>/udp
    # after 4 s rewrite classification to "RESTRICTED"; after 4 more s stop both
    # container log: "UNCLASSIFIED" labels first, then "RESTRICTED" labels, never interleaved after the switch

def test_watchdog_follows_classification(data_dir, tmp_path):
    # as above, container started with --cap-add NET_ADMIN and ConfLabelReader -r'UNCLASSIFIED'
    # NEVER use --network host here: iptables in the container would change the host's OUTPUT policy
    # t≈3 s: docker exec iptables-legacy -S OUTPUT == "-P OUTPUT ACCEPT"
    # switch to RESTRICTED → within 6 s "-P OUTPUT DROP"; switch back → within 3 s "-P OUTPUT ACCEPT"
```

- [ ] **Step 2: Run** `python3 -m pytest -m integration -v`. Expected: PASS. A failure here points at a byte-level mismatch: compare against the samples with the Task 2 tests.
- [ ] **Step 3: Commit** (`test: integration tests against ConfLabelReader and iptables watchdog`)

---

### Task 10: Webcam verification on native Linux (manual)

**Files:**
- Create: `docs/webcam-checklist.md`

- [ ] **Step 1: Write the checklist** and run it on the native Ubuntu host (Claude Code via VS Code Remote-SSH):
  1. `v4l2-ctl --list-devices` shows the camera, and `ffmpeg -f v4l2 -list_formats all -i /dev/video0` lists its formats.
  2. `sudo docker run --rm --device /dev/video0 -v $PWD/conf:/conf labelcast` with output `udp://<host>:50000`.
  3. `ffplay udp://@:50000` on the receiver shows live video.
  4. ConfLabelReader container with `-r'UNCLASSIFIED'` → `-P OUTPUT ACCEPT`.
  5. Edit `conf/label.conf` classification → `DROP` within ~5 s; revert → `ACCEPT`.
  6. Unplug the camera → labelcast logs restarts with backoff, and the watchdog drops within ~4 s. Replug → video and labels resume.
- [ ] **Step 2: Record the results** (pass/fail per item, camera model, resolution) in the checklist file.
- [ ] **Step 3: Commit** (`docs: webcam verification checklist and results`)
