import struct

from labelcast.ts import (
    PACKET_SIZE, PacketFramer, SectionAssembler, crc32_mpeg2, parse_pat, payload, pcr, pid, pusi,
)


def _packets(raw: bytes) -> list[bytes]:
    return [raw[i:i + PACKET_SIZE] for i in range(0, len(raw), PACKET_SIZE)]


def _first_section(packets: list[bytes], want_pid: int) -> bytes:
    asm = SectionAssembler()
    for p in packets:
        if pid(p) == want_pid:
            sections = asm.push(p)
            if sections:
                return sections[0]
    raise AssertionError(f"no section on PID {want_pid:#x}")


def _section_with_crc(body: bytes) -> bytes:
    return body + struct.pack(">I", crc32_mpeg2(body))


def _psi_packets(pid_: int, section: bytes) -> list[bytes]:
    data = b"\x00" + section  # pointer_field
    out = []
    first = True
    while data:
        chunk, data = data[:184], data[184:]
        hdr = bytes([0x47, (0x40 if first else 0) | (pid_ >> 8), pid_ & 0xFF, 0x10])
        out.append(hdr + chunk + b"\xff" * (184 - len(chunk)))
        first = False
    return out


def test_crc32_mpeg2_vector():
    assert crc32_mpeg2(b"123456789") == 0x0376E6E7


def test_framer_split_chunks(data_dir):
    raw = (data_dir / "foreman_cif_klv.ts").read_bytes()
    f = PacketFramer()
    out = [p for i in range(0, len(raw), 1000) for p in f.feed(raw[i:i + 1000])]
    assert b"".join(out) == raw
    assert f.resyncs == 0


def test_framer_resyncs_after_garbage(data_dir):
    raw = (data_dir / "foreman_cif_klv.ts").read_bytes()[:PACKET_SIZE * 10]
    f = PacketFramer()
    out = f.feed(raw[:PACKET_SIZE * 3] + b"\x00\x47\x13" + raw[PACKET_SIZE * 3:])
    assert out == _packets(raw)
    assert f.resyncs == 1


def test_section_assembler_pat_and_pmt(data_dir):
    packets = _packets((data_dir / "foreman_cif_klv_4774xml.ts").read_bytes())
    pat = _first_section(packets, 0)
    assert parse_pat(pat) == [(1, 0x1000)]
    pmt = _first_section(packets, 0x1000)
    assert pmt[0] == 0x02
    assert crc32_mpeg2(pmt) == 0


def test_section_assembler_spanning_two_packets():
    body = bytes([0x02, 0xB0 | ((300 - 3) >> 8), (300 - 3) & 0xFF]) + (bytes(range(256)) * 2)[:300 - 3 - 4]
    section = _section_with_crc(body)
    assert len(section) == 300
    p1, p2 = _psi_packets(0x1000, section)
    asm = SectionAssembler()
    assert asm.push(p1) == []
    assert asm.push(p2) == [section]
    assert asm.crc_errors == 0


def test_section_assembler_rejects_bad_crc():
    body = bytes([0x00, 0xB0, 0x0D, 0x00, 0x01, 0xC1, 0x00, 0x00, 0x00, 0x01, 0xF0, 0x00])
    section = bytearray(_section_with_crc(body))
    section[8] ^= 0xFF
    asm = SectionAssembler()
    assert asm.push(_psi_packets(0, bytes(section))[0]) == []
    assert asm.crc_errors == 1


def test_packet_header_accessors(data_dir):
    packets = _packets((data_dir / "foreman_cif_klv.ts").read_bytes())
    pat = next(p for p in packets if pid(p) == 0)
    assert pusi(pat)
    assert len(payload(pat)) == 184
    assert pid(packets[0]) == 0x11  # first packet of the sample is the SDT


def test_pcr_extraction(data_dir):
    packets = _packets((data_dir / "foreman_cif_klv.ts").read_bytes())
    values = [pcr(p) for p in packets if pid(p) == 0x100]
    found = [v for v in values if v is not None]
    assert found and found[0] > 0
    assert found == sorted(found)
    assert pcr(packets[0]) is None
