"""Test helpers: run TS through an Injector and analyse the output with labelcast.ts."""

from pathlib import Path

from labelcast.ts import (
    PACKET_SIZE, PacketFramer, Pmt, SectionAssembler, is_label_element, parse_pat, parse_pmt, payload, pcr,
    pid, pusi,
)


def packets_of(ts: bytes) -> list[bytes]:
    return [ts[i:i + PACKET_SIZE] for i in range(0, len(ts) - PACKET_SIZE + 1, PACKET_SIZE)]


def run(inj, path: Path) -> bytes:
    framer = PacketFramer()
    out = []
    for pkt in framer.feed(path.read_bytes()):
        out += inj.process(pkt)
    return b"".join(out)


def pmts_in(ts: bytes) -> list[Pmt]:
    """All PMT sections in order of appearance."""
    pat_asm, pmt_asm, pmt_pid = SectionAssembler(), SectionAssembler(), None
    found = []
    for pkt in packets_of(ts):
        if pid(pkt) == 0:
            for sec in pat_asm.push(pkt):
                pmt_pid = parse_pat(sec)[0][1]
        elif pid(pkt) == pmt_pid:
            found += [parse_pmt(sec) for sec in pmt_asm.push(pkt)]
    return found


def first_pmt(ts: bytes) -> Pmt:
    return pmts_in(ts)[0]


def labels_in(ts: bytes) -> list[tuple[int, bytes]]:
    """(pid, payload) of every complete label PES on a PID that a preceding PMT declares as a label."""
    pat_asm, pmt_asm, pmt_pid = SectionAssembler(), SectionAssembler(), None
    label_pids: set[int] = set()
    partial: dict[int, bytearray] = {}
    out = []
    for pkt in packets_of(ts):
        p = pid(pkt)
        if p == 0:
            for sec in pat_asm.push(pkt):
                pmt_pid = parse_pat(sec)[0][1]
        elif p == pmt_pid:
            for sec in pmt_asm.push(pkt):
                label_pids = {e.pid for e in parse_pmt(sec).elements if is_label_element(e)}
        elif p in label_pids:
            if pusi(pkt):
                partial[p] = bytearray()
            if p in partial:
                partial[p] += payload(pkt)
                buf = partial[p]
                if len(buf) >= 6 and len(buf) >= 6 + ((buf[4] << 8) | buf[5]):
                    total = 6 + ((buf[4] << 8) | buf[5])
                    out.append((p, bytes(buf[9 + buf[8]:total])))
                    del partial[p]
    return out


def label_packet_indices(ts: bytes, label_pid: int) -> list[int]:
    return [i for i, pkt in enumerate(packets_of(ts)) if pid(pkt) == label_pid and pusi(pkt)]


def stream_seconds(ts: bytes, pcr_pid: int = 0x100) -> float:
    values = [v for pkt in packets_of(ts) if pid(pkt) == pcr_pid and (v := pcr(pkt)) is not None]
    return (values[-1] - values[0]) / 27_000_000
