"""MPEG-2 Transport Stream primitives (ISO/IEC 13818-1). Pure functions, no I/O."""

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
            # Lookahead (next sync byte) is only used while resyncing, so a valid
            # packet directly before garbage is not lost.
            if buf[0] == SYNC_BYTE:
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
