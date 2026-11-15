"""MPEG-2 Transport Stream primitives (ISO/IEC 13818-1). Pure functions, no I/O."""

import struct
from dataclasses import dataclass, field

PACKET_SIZE = 188
SYNC_BYTE = 0x47


def _make_crc_table() -> list[int]:
    table = []
    for i in range(256):
        c = i << 24
        for _ in range(8):
            c = ((c << 1) ^ 0x04C11DB7) if c & 0x80000000 else (c << 1)
        table.append(c & 0xFFFFFFFF)
    return table


_CRC_TABLE = _make_crc_table()


def crc32_mpeg2(data: bytes) -> int:
    """CRC-32/MPEG-2: poly 0x04C11DB7, init 0xFFFFFFFF, no reflection, no final XOR."""
    crc = 0xFFFFFFFF
    for b in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC_TABLE[((crc >> 24) ^ b) & 0xFF]
    return crc


class PacketFramer:
    """Splits an arbitrary byte stream into 188-byte TS packets, resyncing on garbage."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.resyncs = 0

    def feed(self, data: bytes) -> list[bytes]:
        buf = self._buf
        buf += data
        out: list[bytes] = []
        while len(buf) >= PACKET_SIZE:
            if buf[0] == SYNC_BYTE:
                # Confirm the packet with the next sync byte, so that a cut-off packet
                # (which also starts with 0x47) is not passed on as a whole one.
                if len(buf) <= PACKET_SIZE:
                    break  # wait for one more byte (or flush() at end of input)
                if buf[PACKET_SIZE] != SYNC_BYTE:
                    if len(buf) < 2 * PACKET_SIZE:
                        break  # not enough data yet to tell truncation from trailing garbage
                    restart = self._find_restart(buf)
                    if restart is not None:  # this packet is truncated: the real one starts here
                        self.resyncs += 1
                        del buf[:restart]
                        continue
                    # otherwise: a whole packet followed by garbage; keep the packet
                out.append(bytes(buf[:PACKET_SIZE]))
                del buf[:PACKET_SIZE]
                continue
            self.resyncs += 1
            start = self._find_sync(buf)
            if start is None:
                # keep a tail that may hold the start of the next packet
                del buf[:len(buf) - (PACKET_SIZE - 1)]
                break
            del buf[:start]
        return out

    def flush(self) -> list[bytes]:
        """At end of input: return the remaining whole packets without lookahead."""
        buf = self._buf
        out = []
        while len(buf) >= PACKET_SIZE and buf[0] == SYNC_BYTE:
            out.append(bytes(buf[:PACKET_SIZE]))
            del buf[:PACKET_SIZE]
        buf.clear()
        return out

    @staticmethod
    def _find_restart(buf: bytearray) -> int | None:
        """Offset 1..187 where two consecutive sync bytes prove a packet boundary."""
        for k in range(1, PACKET_SIZE):
            if buf[k] == SYNC_BYTE and buf[k + PACKET_SIZE] == SYNC_BYTE:
                return k
        return None

    @staticmethod
    def _find_sync(buf: bytearray) -> int | None:
        i = buf.find(SYNC_BYTE, 1)
        while i != -1:
            if i + PACKET_SIZE >= len(buf) or buf[i + PACKET_SIZE] == SYNC_BYTE:
                return i
            i = buf.find(SYNC_BYTE, i + 1)
        return None


def pid(pkt: bytes) -> int:
    return ((pkt[1] & 0x1F) << 8) | pkt[2]


def pusi(pkt: bytes) -> bool:
    return bool(pkt[1] & 0x40)


def _payload_offset(pkt: bytes) -> int | None:
    afc = (pkt[3] >> 4) & 0x3
    if not afc & 0x1:
        return None
    offset = 4
    if afc & 0x2:
        offset += 1 + pkt[4]
    return offset if offset <= PACKET_SIZE else None


def payload(pkt: bytes) -> bytes:
    offset = _payload_offset(pkt)
    return b"" if offset is None else pkt[offset:]


def pcr(pkt: bytes) -> int | None:
    """PCR in 27 MHz units (base * 300 + extension), or None."""
    if not (pkt[3] & 0x20) or pkt[4] < 7 or not (pkt[5] & 0x10):
        return None
    b = pkt[6:12]
    base = (b[0] << 25) | (b[1] << 17) | (b[2] << 9) | (b[3] << 1) | (b[4] >> 7)
    ext = ((b[4] & 0x01) << 8) | b[5]
    return base * 300 + ext


class SectionAssembler:
    """Reassembles PSI sections of one PID; returns only sections with a valid CRC."""

    def __init__(self) -> None:
        self._buf: bytearray | None = None
        self.crc_errors = 0

    def push(self, pkt: bytes) -> list[bytes]:
        data = payload(pkt)
        if not data:
            return []
        if pusi(pkt):
            pointer = data[0]
            if self._buf is not None:
                self._buf += data[1:1 + pointer]
            done = self._drain()
            self._buf = bytearray(data[1 + pointer:])
        else:
            if self._buf is None:
                return []
            self._buf += data
            done = []
        return done + self._drain()

    def _drain(self) -> list[bytes]:
        out: list[bytes] = []
        buf = self._buf
        while buf is not None and len(buf) >= 3:
            if buf[0] == 0xFF:
                buf = None
                break
            length = 3 + (((buf[1] & 0x0F) << 8) | buf[2])
            if len(buf) < length:
                break
            section = bytes(buf[:length])
            del buf[:length]
            if crc32_mpeg2(section) == 0:
                out.append(section)
            else:
                self.crc_errors += 1
        self._buf = buf
        return out


def parse_pat(section: bytes) -> list[tuple[int, int]]:
    """Returns (program_number, pmt_pid) for each program except program 0 (NIT)."""
    end = 3 + (((section[1] & 0x0F) << 8) | section[2]) - 4
    programs = []
    for i in range(8, end, 4):
        number = (section[i] << 8) | section[i + 1]
        if number != 0:
            programs.append((number, ((section[i + 2] & 0x1F) << 8) | section[i + 3]))
    return programs


# --- PMT -----------------------------------------------------------------

LABEL_FORMATS = (b"$XML", b"$EXI", b"$BRL")


@dataclass
class PmtElement:
    stream_type: int
    pid: int
    es_info: bytes


@dataclass
class Pmt:
    program_number: int
    version: int
    pcr_pid: int
    program_info: bytes
    elements: list[PmtElement] = field(default_factory=list)


def parse_pmt(section: bytes) -> Pmt:
    end = 3 + (((section[1] & 0x0F) << 8) | section[2]) - 4
    program_info_length = ((section[10] & 0x0F) << 8) | section[11]
    pmt = Pmt(
        program_number=(section[3] << 8) | section[4],
        version=(section[5] >> 1) & 0x1F,
        pcr_pid=((section[8] & 0x1F) << 8) | section[9],
        program_info=bytes(section[12:12 + program_info_length]),
    )
    i = 12 + program_info_length
    while i + 5 <= end:
        es_len = ((section[i + 3] & 0x0F) << 8) | section[i + 4]
        pmt.elements.append(PmtElement(
            stream_type=section[i],
            pid=((section[i + 1] & 0x1F) << 8) | section[i + 2],
            es_info=bytes(section[i + 5:i + 5 + es_len]),
        ))
        i += 5 + es_len
    return pmt


def build_pmt_section(pmt: Pmt) -> bytes:
    body = struct.pack(">HBBBHH", pmt.program_number, 0xC1 | ((pmt.version & 0x1F) << 1), 0, 0,
                       0xE000 | pmt.pcr_pid, 0xF000 | len(pmt.program_info)) + pmt.program_info
    for el in pmt.elements:
        body += struct.pack(">BHH", el.stream_type, 0xE000 | el.pid, 0xF000 | len(el.es_info)) + el.es_info
    section_length = len(body) + 4
    head = bytes([0x02, 0xB0 | (section_length >> 8), section_length & 0xFF])
    section = head + body
    return section + struct.pack(">I", crc32_mpeg2(section))


def label_element(pid: int, fmt: bytes) -> PmtElement:
    """4774 label program element per the STANAG 4609 binding profile (Table 2)."""
    registration = b"\x05\x08" + fmt + b"4774"
    metadata = b"\x26\x0e\x01\x04\xff" + fmt + b"\x01\x2f\x04" + b"4774"
    return PmtElement(0x06, pid, registration + metadata)


def is_label_element(el: PmtElement) -> bool:
    if el.stream_type not in (0x06, 0x15):
        return False
    info = el.es_info
    i = 0
    while i + 2 <= len(info):
        tag, length = info[i], info[i + 1]
        if tag == 0x05 and length >= 4 and info[i + 2:i + 6] in LABEL_FORMATS:
            return True
        i += 2 + length
    return False


# --- packetization -------------------------------------------------------

class ContinuityCounter:
    def __init__(self) -> None:
        self._value = -1

    def next(self) -> int:
        self._value = (self._value + 1) & 0x0F
        return self._value


def _packetize(pid: int, data: bytes, cc: ContinuityCounter, fill_with_af: bool) -> list[bytes]:
    packets = []
    first = True
    offset = 0
    while offset < len(data) or first:
        chunk = data[offset:offset + 184]
        offset += len(chunk)
        b1 = (0x40 if first else 0x00) | (pid >> 8)
        if len(chunk) == 184 or not fill_with_af:
            hdr = bytes([SYNC_BYTE, b1, pid & 0xFF, 0x10 | cc.next()])
            packets.append(hdr + chunk + b"\xff" * (184 - len(chunk)))
        else:
            af_len = 183 - len(chunk)
            af = bytes([af_len]) + ((b"\x00" + b"\xff" * (af_len - 1)) if af_len else b"")
            hdr = bytes([SYNC_BYTE, b1, pid & 0xFF, 0x30 | cc.next()])
            packets.append(hdr + af + chunk)
        first = False
    return packets


def packetize_section(pid: int, section: bytes, cc: ContinuityCounter) -> list[bytes]:
    """PSI section → TS packets: pointer_field 0, remainder filled with 0xFF."""
    return _packetize(pid, b"\x00" + section, cc, fill_with_af=False)


def build_label_pes(payload: bytes) -> bytes:
    """Asynchronous label PES: private_stream_1, data_alignment + original, no PTS."""
    if len(payload) > 65000:
        raise ValueError(f"label payload too large: {len(payload)} > 65000 bytes")
    return b"\x00\x00\x01\xbd" + struct.pack(">H", 3 + len(payload)) + b"\x85\x00\x00" + payload


def packetize_pes(pid: int, pes: bytes, cc: ContinuityCounter) -> list[bytes]:
    """PES → TS packets; the last packet is padded with adaptation-field stuffing."""
    return _packetize(pid, pes, cc, fill_with_af=True)
