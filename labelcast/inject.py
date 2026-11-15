"""Stateful TS processing: PMT rewrite and PCR-timed STANAG 4774 label insertion."""

import logging
import time
from typing import Callable

from .label import Label
from .ts import (
    ContinuityCounter, Pmt, SectionAssembler, build_label_pes, build_pmt_section, is_label_element,
    label_element, packetize_pes, packetize_section, parse_pat, parse_pmt, pcr, pid,
)

PCR_HZ = 27_000_000
PCR_WRAP = (1 << 33) * 300
DISCONTINUITY_TICKS = 10 * PCR_HZ
PCR_TIMEOUT = 2.0
FIRST_FREE_PID = 0x0102


class Injector:
    def __init__(self, label_pid: int = 0x0102, clock: Callable[[], float] = time.monotonic,
                 log: logging.Logger | None = None) -> None:
        self._wanted_pid = label_pid
        self._clock = clock
        self._log = log or logging.getLogger("labelcast.inject")
        self._label: Label | None = None
        self._interval = 1.0
        self._label_pending = False
        self._pmt_pending = False
        self._bumps = 0
        self._emitted_fmt: bytes | None = None
        self._last_version: int | None = None
        self._cc_pmt = ContinuityCounter()
        self._cc_label: dict[int, ContinuityCounter] = {}
        self._warned_mpts = False
        self._fallback = False
        self.active_label_pid: int | None = None
        self.stats = {"packets_in": 0, "packets_out": 0, "labels": 0, "pmt_versions": 0, "dropped": 0}
        self.reset()

    # -- public API --------------------------------------------------------

    def reset(self) -> None:
        self._pat_asm = SectionAssembler()
        self._pmt_asm = SectionAssembler()
        self._pmt_pid: int | None = None
        self._src_pmt: Pmt | None = None
        self._pcr_pid: int | None = None
        self._drop_pids: set[int] = set()
        self.pmt_seen = False
        self._pcr: int | None = None
        self._last_label_pcr: int | None = None
        self._last_label_wall: float | None = None
        self._last_pcr_wall = self._clock()
        self._label_pending = True

    def set_label(self, label: Label, interval: float) -> None:
        if self._label is not None and label.fmt != self._label.fmt and self._src_pmt is not None:
            self._pmt_pending = True
        self._label = label
        self._interval = interval
        self._label_pending = True

    def process(self, pkt: bytes) -> list[bytes]:
        self.stats["packets_in"] += 1
        p = pid(pkt)
        out: list[bytes] = []
        if p == 0:
            out.append(pkt)
            for section in self._pat_asm.push(pkt):
                self._on_pat(section)
        elif p == self._pmt_pid:
            for section in self._pmt_asm.push(pkt):
                if section[0] == 0x02:
                    self._src_pmt = parse_pmt(section)
                    out += self._emit_pmt()
        elif p in self._drop_pids:
            self.stats["dropped"] += 1
        else:
            out.append(pkt)

        if p == self._pcr_pid:
            value = pcr(pkt)
            if value is not None:
                self._on_pcr(value)

        if self._pmt_pending and self._src_pmt is not None:
            out += self._emit_pmt()
        if self._label_due():
            out += self._emit_label()
        self.stats["packets_out"] += len(out)
        return out

    # -- internals ---------------------------------------------------------

    def _on_pat(self, section: bytes) -> None:
        programs = parse_pat(section)
        if not programs:
            return
        if len(programs) > 1 and not self._warned_mpts:
            self._warned_mpts = True
            self._log.warning("transport stream has %d programs; using program %d only",
                              len(programs), programs[0][0])
        pmt_pid = programs[0][1]
        if pmt_pid != self._pmt_pid:
            self._pmt_pid = pmt_pid
            self._pmt_asm = SectionAssembler()

    def _on_pcr(self, value: int) -> None:
        self._last_pcr_wall = self._clock()
        if self._fallback:
            self._fallback = False
            self._log.info("PCR found again; label timing back on stream clock")
        if self._pcr is not None and (value - self._pcr) % PCR_WRAP > DISCONTINUITY_TICKS:
            self._log.info("PCR discontinuity; restarting label timer")
            self._label_pending = True
            self._last_label_pcr = None
        self._pcr = value

    def _choose_label_pid(self, pmt: Pmt, keep_pids: set[int]) -> int:
        used = keep_pids | {0, self._pmt_pid, pmt.pcr_pid}
        if self._wanted_pid not in used:
            return self._wanted_pid
        candidate = FIRST_FREE_PID
        while candidate in used:
            candidate += 1
        return candidate

    def _emit_pmt(self) -> list[bytes]:
        src = self._src_pmt
        self._pmt_pending = False
        labels = [e for e in src.elements if is_label_element(e)]
        keep = [e for e in src.elements if not is_label_element(e)]
        label_pid = self._choose_label_pid(src, {e.pid for e in keep})
        if label_pid != self.active_label_pid:
            if label_pid != self._wanted_pid:
                self._log.warning("label PID %#06x is in use; using %#06x", self._wanted_pid, label_pid)
            self.active_label_pid = label_pid
        self._drop_pids = {e.pid for e in labels}
        self._pcr_pid = src.pcr_pid

        fmt = self._label.fmt if self._label is not None else b"$XML"
        if self._emitted_fmt is not None and fmt != self._emitted_fmt:
            self._bumps += 1
        self._emitted_fmt = fmt
        version = (src.version + self._bumps) % 32
        if version != self._last_version:
            self._last_version = version
            self.stats["pmt_versions"] += 1

        ours = Pmt(src.program_number, version, src.pcr_pid, src.program_info,
                   keep + [label_element(label_pid, fmt)])
        self.pmt_seen = True
        return packetize_section(self._pmt_pid, build_pmt_section(ours), self._cc_pmt)

    def _label_due(self) -> bool:
        if not self.pmt_seen or self._label is None:
            return False
        if self._label_pending or self._last_label_wall is None:
            return True
        now = self._clock()
        if not self._fallback and now - self._last_pcr_wall > PCR_TIMEOUT:
            self._fallback = True
            self._log.warning("no PCR for %.0f s; label timing on wall clock", PCR_TIMEOUT)
        if self._fallback:
            return now - self._last_label_wall >= self._interval
        if self._pcr is None:
            return False
        if self._last_label_pcr is None:
            self._last_label_pcr = self._pcr  # first PCR after a label: start timing here
            return False
        return (self._pcr - self._last_label_pcr) % PCR_WRAP >= self._interval * PCR_HZ

    def _emit_label(self) -> list[bytes]:
        cc = self._cc_label.setdefault(self.active_label_pid, ContinuityCounter())
        pkts = packetize_pes(self.active_label_pid, build_label_pes(self._label.payload), cc)
        self._label_pending = False
        self._last_label_wall = self._clock()
        self._last_label_pcr = self._pcr
        self.stats["labels"] += 1
        return pkts
