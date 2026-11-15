import pytest

from labelcast.ts import (
    ContinuityCounter, PmtElement, build_label_pes, build_pmt_section, crc32_mpeg2, is_label_element,
    label_element, packetize_pes, packetize_section, parse_pmt, payload, pid, pusi,
)

XML_EL = bytes.fromhex("050824584d4c34373734260e0104ff24584d4c012f0434373734")
BRL_EL = bytes.fromhex("05082442524c34373734260e0104ff2442524c012f0434373734")


def _cc(pkt: bytes) -> int:
    return pkt[3] & 0x0F


def test_label_element_bytes_match_samples():
    assert label_element(0x102, b"$XML") == PmtElement(0x06, 0x102, XML_EL)
    assert label_element(0x102, b"$BRL").es_info == BRL_EL


def test_parse_pmt_fields(xml_sample_pmt_section):
    pmt = parse_pmt(xml_sample_pmt_section)
    assert pmt.program_number == 1
    assert pmt.pcr_pid == 0x100
    assert [(e.stream_type, e.pid) for e in pmt.elements] == [(0x1B, 0x100), (0x15, 0x101), (0x06, 0x102)]
    assert pmt.elements[2].es_info == XML_EL


def test_parse_build_roundtrip(xml_sample_pmt_section):
    assert build_pmt_section(parse_pmt(xml_sample_pmt_section)) == xml_sample_pmt_section


def test_build_sets_version_and_valid_crc(xml_sample_pmt_section):
    pmt = parse_pmt(xml_sample_pmt_section)
    pmt.version = 7
    section = build_pmt_section(pmt)
    assert (section[5] >> 1) & 0x1F == 7
    assert crc32_mpeg2(section) == 0


def test_is_label_element(exi_sample_pmt):
    assert [is_label_element(e) for e in exi_sample_pmt.elements] == [False, False, True]


def test_packetize_section_single_packet(xml_sample_pmt_section):
    cc = ContinuityCounter()
    pkts = packetize_section(0x1000, xml_sample_pmt_section, cc)
    assert len(pkts) == 1 and len(pkts[0]) == 188
    assert pid(pkts[0]) == 0x1000 and pusi(pkts[0])
    data = payload(pkts[0])
    assert data[0] == 0 and data[1:1 + len(xml_sample_pmt_section)] == xml_sample_pmt_section
    assert set(data[1 + len(xml_sample_pmt_section):]) == {0xFF}


def test_label_pes_header():
    pes = build_label_pes(b"x" * 705)
    assert pes[:9] == bytes.fromhex("000001bd02c4850000")
    assert len(pes) == 9 + 705


def test_label_pes_too_large():
    with pytest.raises(ValueError):
        build_label_pes(b"x" * 65001)


def test_packetize_pes_lengths_and_stuffing():
    cc = ContinuityCounter()
    pes = build_label_pes(b"y" * 400)
    pkts = packetize_pes(0x102, pes, cc)
    assert all(len(p) == 188 for p in pkts)
    assert pusi(pkts[0]) and not any(pusi(p) for p in pkts[1:])
    assert all(pid(p) == 0x102 for p in pkts)
    assert b"".join(payload(p) for p in pkts) == pes
    assert [_cc(p) for p in pkts] == list(range(len(pkts)))


def test_packetize_pes_exact_multiple():
    pes = build_label_pes(b"z" * (184 * 2 - 9))
    pkts = packetize_pes(0x102, pes, ContinuityCounter())
    assert len(pkts) == 2
    assert all((p[3] >> 4) & 0x3 == 0x1 for p in pkts)  # payload only, no adaptation field
    assert b"".join(payload(p) for p in pkts) == pes


def test_packetize_pes_one_byte_short():
    # 183 bytes in the last packet → adaptation field of length 0 (1 byte)
    pes = build_label_pes(b"q" * (184 + 183 - 9))
    pkts = packetize_pes(0x102, pes, ContinuityCounter())
    assert len(pkts) == 2 and pkts[1][4] == 0
    assert b"".join(payload(p) for p in pkts) == pes


def test_continuity_counter_wraps():
    cc = ContinuityCounter()
    assert [cc.next() for _ in range(18)] == list(range(16)) + [0, 1]
