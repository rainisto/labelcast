from datetime import datetime, timezone

import pytest

from labelcast.config import LabelConfig
from labelcast.inject import Injector
from labelcast.label import make_label
from labelcast.ts import (
    ContinuityCounter, PacketFramer, build_pmt_section, packetize_section, parse_pmt, pid, pusi,
)
from tests.conftest import DATA_DIR, first_section
from tests.helpers import first_pmt, labels_in, packets_of, pmts_in, run, stream_seconds

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
XML_LABEL = make_label(LabelConfig(policy="NATO", classification="UNCLASSIFIED"), NOW)
BRL_LABEL = make_label(LabelConfig(encoding="brl", policy="NATO", classification="RESTRICTED"), NOW)
XML_EL = bytes.fromhex("050824584d4c34373734260e0104ff24584d4c012f0434373734")
BRL_EL = bytes.fromhex("05082442524c34373734260e0104ff2442524c012f0434373734")
WRAP = (1 << 33) * 300


def _sample_packets(name: str) -> list[bytes]:
    return PacketFramer().feed((DATA_DIR / name).read_bytes())


def _feed(inj: Injector, packets: list[bytes]) -> list[list[bytes]]:
    return [inj.process(p) for p in packets]


def _pcr_packet(pid_: int, value: int, cc: int) -> bytes:
    base, ext = divmod(value, 300)
    af = bytes([7, 0x10, (base >> 25) & 0xFF, (base >> 17) & 0xFF, (base >> 9) & 0xFF, (base >> 1) & 0xFF,
                ((base & 1) << 7) | 0x7E | (ext >> 8), ext & 0xFF])
    body = af + b"\xff" * (184 - len(af))
    return bytes([0x47, pid_ >> 8, pid_ & 0xFF, 0x30 | cc]) + body


def _psi_prefix() -> list[bytes]:
    """PAT + PMT packets of the unlabelled sample (PCR PID 0x100)."""
    pat = packetize_section(0, first_section(DATA_DIR / "foreman_cif_klv.ts", 0), ContinuityCounter())
    pmt = packetize_section(0x1000, first_section(DATA_DIR / "foreman_cif_klv.ts", 0x1000), ContinuityCounter())
    return pat + pmt


def test_adds_label_to_unlabelled_sample(data_dir):
    inj = Injector()
    inj.set_label(XML_LABEL, 1.0)
    out = run(inj, data_dir / "foreman_cif_klv.ts")
    pmt = first_pmt(out)
    assert [e.stream_type for e in pmt.elements] == [0x1B, 0x15, 0x06]
    assert pmt.elements[2].pid == 0x102 and pmt.elements[2].es_info == XML_EL
    labels = labels_in(out)
    assert labels and all(p == (0x102, XML_LABEL.payload) for p in labels)
    assert abs(len(labels) - round(stream_seconds(out))) <= 1
    assert inj.stats["labels"] == len(labels)


def test_replaces_exi_label(data_dir):
    inj = Injector()
    inj.set_label(XML_LABEL, 1.0)
    out = run(inj, data_dir / "foreman_cif_klv_4774.ts")
    pmt = first_pmt(out)
    label_els = [e for e in pmt.elements if e.stream_type == 0x06]
    assert len(label_els) == 1 and label_els[0].es_info == XML_EL
    assert all(p == XML_LABEL.payload for _, p in labels_in(out))
    assert any(pid(p) == 0x101 for p in packets_of(out))  # KLV preserved
    assert inj.stats["dropped"] > 0


def test_pid_conflict_moves_label(data_dir):
    inj = Injector(label_pid=0x101)  # 0x101 is the KLV stream
    inj.set_label(XML_LABEL, 1.0)
    out = run(inj, data_dir / "foreman_cif_klv_4774.ts")
    assert inj.active_label_pid == 0x102
    assert {p for p, _ in labels_in(out)} == {0x102}


def test_label_change_bumps_version_on_fmt_change():
    packets = _sample_packets("foreman_cif_klv.ts")
    inj = Injector()
    inj.set_label(XML_LABEL, 1.0)
    half = len(packets) // 2
    out = _feed(inj, packets[:half])
    inj.set_label(BRL_LABEL, 1.0)
    out += _feed(inj, packets[half:])
    ts = b"".join(b"".join(o) for o in out)
    pmts = pmts_in(ts)
    assert pmts[-1].version == (pmts[0].version + 1) % 32
    assert pmts[0].elements[-1].es_info == XML_EL and pmts[-1].elements[-1].es_info == BRL_EL
    payloads = [p for _, p in labels_in(ts)]
    first_brl = payloads.index(BRL_LABEL.payload)
    assert all(p == XML_LABEL.payload for p in payloads[:first_brl])
    assert all(p == BRL_LABEL.payload for p in payloads[first_brl:])


def test_same_fmt_change_keeps_version():
    packets = _sample_packets("foreman_cif_klv.ts")
    inj = Injector()
    inj.set_label(XML_LABEL, 1.0)
    out = _feed(inj, packets[:2000])
    inj.set_label(make_label(LabelConfig(policy="NATO", classification="SECRET"), NOW), 1.0)
    out += _feed(inj, packets[2000:])
    versions = {p.version for p in pmts_in(b"".join(b"".join(o) for o in out))}
    assert len(versions) == 1


def test_new_label_emitted_immediately():
    packets = _sample_packets("foreman_cif_klv.ts")
    inj = Injector()
    inj.set_label(XML_LABEL, 1.0)
    _feed(inj, packets[:2000])
    new = make_label(LabelConfig(policy="NATO", classification="SECRET"), NOW)
    inj.set_label(new, 1.0)
    nxt = b"".join(b"".join(o) for o in _feed(inj, packets[2000:2002]))
    assert any(pid(p) == 0x102 and pusi(p) for p in packets_of(nxt))


def test_no_labels_before_pmt():
    inj = Injector()
    inj.set_label(XML_LABEL, 1.0)
    packets = [p for p in _sample_packets("foreman_cif_klv.ts") if pid(p) in (0, 0x100)]
    out = b"".join(b"".join(o) for o in _feed(inj, packets))
    assert not any(pid(p) == 0x102 for p in packets_of(out))
    assert not inj.pmt_seen


def test_pcr_discontinuity_backwards():
    packets = _sample_packets("foreman_cif_klv.ts")
    inj = Injector()
    inj.set_label(XML_LABEL, 5.0)  # long interval: only the discontinuity can trigger a label
    _feed(inj, packets)
    second = _feed(inj, packets)
    first_pcr_idx = next(i for i, p in enumerate(packets) if pid(p) == 0x100 and (p[3] & 0x20) and p[5] & 0x10)
    window = b"".join(b"".join(o) for o in second[:first_pcr_idx + 2])
    assert any(pid(p) == 0x102 and pusi(p) for p in packets_of(window))


def test_pcr_wraparound():
    inj = Injector()
    inj.set_label(XML_LABEL, 1.0)
    start = WRAP - int(0.5 * 27_000_000)
    pkts = _psi_prefix()
    for i in range(30):  # 3.0 s in 0.1 s steps, crossing the wrap point
        pkts.append(_pcr_packet(0x100, (start + i * 2_700_000) % WRAP, i & 0xF))
    out = b"".join(b"".join(o) for o in _feed(inj, pkts))
    assert len(labels_in(out)) == 3


def test_wallclock_fallback():
    t = [0.0]

    def clock() -> float:
        return t[0]

    inj = Injector(clock=clock)
    inj.set_label(XML_LABEL, 1.0)
    out = []
    pkts = _psi_prefix() + [bytes([0x47, 0x01, 0x00, 0x10 | (i & 0xF)]) + b"\x00" * 184 for i in range(20)]
    for p in pkts:
        out += inj.process(p)
        t[0] += 0.5
    n = len(labels_in(b"".join(out)))
    assert 8 <= n <= 11


def test_reset_pauses_labels():
    packets = _sample_packets("foreman_cif_klv.ts")
    inj = Injector()
    inj.set_label(XML_LABEL, 1.0)
    _feed(inj, packets[:1000])
    inj.reset()
    assert not inj.pmt_seen
    no_psi = [p for p in packets[1000:3000] if pid(p) not in (0, 0x1000)]
    out = b"".join(b"".join(o) for o in _feed(inj, no_psi))
    assert not any(pid(p) == 0x102 for p in packets_of(out))
    out = b"".join(b"".join(o) for o in _feed(inj, packets))
    assert labels_in(out)


@pytest.mark.parametrize("watch_pid", [0x1000, 0x102])
def test_continuity_counters_valid(data_dir, watch_pid):
    inj = Injector()
    inj.set_label(XML_LABEL, 0.2)
    out = run(inj, data_dir / "foreman_cif_klv_4774.ts")
    ccs = [p[3] & 0xF for p in packets_of(out) if pid(p) == watch_pid]
    assert len(ccs) > 3
    assert all((b - a) % 16 == 1 for a, b in zip(ccs, ccs[1:]))


def test_rebuilt_pmt_keeps_source_fields(data_dir):
    inj = Injector()
    inj.set_label(XML_LABEL, 1.0)
    out = run(inj, data_dir / "foreman_cif_klv.ts")
    src = parse_pmt(first_section(data_dir / "foreman_cif_klv.ts", 0x1000))
    ours = first_pmt(out)
    assert (ours.program_number, ours.pcr_pid, ours.program_info) == (src.program_number, src.pcr_pid, src.program_info)
    assert ours.elements[:2] == src.elements
    assert build_pmt_section(ours)  # round-trips


def test_crc_errors_counted_and_kept_over_reset():
    pkts = _psi_prefix()
    bad = bytearray(pkts[1])  # PMT packet
    bad[10] ^= 0xFF
    inj = Injector()
    inj.process(pkts[0])
    inj.process(bytes(bad))
    assert inj.stats["crc_errors"] == 1
    inj.reset()
    inj.process(pkts[0])
    inj.process(bytes(bad))
    assert inj.stats["crc_errors"] == 2


def test_changed_pmt_content_after_restart_bumps_version():
    """After an ffmpeg restart the source PMT version may restart from the same number with
    different content; our PMT must still get a new version_number or receivers ignore it."""
    from labelcast.ts import PmtElement, build_pmt_section
    from tests.helpers import pmts_in
    inj = Injector()
    inj.set_label(XML_LABEL, 1.0)
    first = b"".join(b"".join(o) for o in _feed(inj, _psi_prefix()))
    inj.reset()
    src = parse_pmt(first_section(DATA_DIR / "foreman_cif_klv.ts", 0x1000))
    src.elements.append(PmtElement(0x0F, 0x103, b""))  # e.g. audio added, same source version
    pat = packetize_section(0, first_section(DATA_DIR / "foreman_cif_klv.ts", 0), ContinuityCounter())
    pmt = packetize_section(0x1000, build_pmt_section(src), ContinuityCounter())
    second = b"".join(b"".join(o) for o in _feed(inj, pat + pmt))
    v1, v2 = pmts_in(first)[0], pmts_in(second)[0]
    assert v1.version != v2.version
    assert [e.pid for e in v2.elements] == [0x100, 0x101, 0x103, 0x102]


def test_unchanged_pmt_after_restart_keeps_version():
    inj = Injector()
    inj.set_label(XML_LABEL, 1.0)
    first = b"".join(b"".join(o) for o in _feed(inj, _psi_prefix()))
    inj.reset()
    second = b"".join(b"".join(o) for o in _feed(inj, _psi_prefix()))
    from tests.helpers import pmts_in
    assert pmts_in(first)[0].version == pmts_in(second)[0].version
