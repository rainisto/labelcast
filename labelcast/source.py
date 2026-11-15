"""TS sources: ffmpeg subprocess (restart with backoff), UDP listener, stdin."""

import ipaddress
import logging
import os
import selectors
import socket
import struct
import subprocess
import sys
import time
from typing import Protocol

from .config import SourceConfig
from .output import parse_udp_url

FFMPEG_PREFIX = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
BACKOFF_START = 1.0
BACKOFF_MAX = 10.0
HEALTHY_AFTER = 10.0
READ_SIZE = 65536


class Source(Protocol):
    restarts: int

    def read(self, timeout: float) -> bytes: ...
    def close(self) -> None: ...


class FfmpegSource:
    def __init__(self, cfg: SourceConfig, log: logging.Logger, cmd: list[str] | None = None,
                 start: bool = True) -> None:
        self.command = [*(cmd or FFMPEG_PREFIX), *cfg.args, "-f", "mpegts", "-"]
        self._log = log
        self.restarts = 0
        self._backoff = BACKOFF_START
        self._proc: subprocess.Popen | None = None
        self._sel = selectors.DefaultSelector()
        self._restart_at = 0.0
        self._first_data: float | None = None
        self._stderr_buf = b""
        if start:
            self._start()

    def _start(self) -> None:
        self._log.info("starting: %s", " ".join(self.command))
        self._proc = subprocess.Popen(self.command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE)
        self._sel.register(self._proc.stdout, selectors.EVENT_READ, "out")
        self._sel.register(self._proc.stderr, selectors.EVENT_READ, "err")
        self._first_data = None

    def read(self, timeout: float) -> bytes:
        if self._proc is None:
            wait = self._restart_at - time.monotonic()
            if wait > 0:
                time.sleep(min(timeout, wait))
                return b""
            self.restarts += 1
            self._start()
        data = b""
        for key, _ in self._sel.select(timeout):
            chunk = os.read(key.fileobj.fileno(), READ_SIZE)
            if key.data == "err":
                self._log_stderr(chunk)
                if not chunk:  # stderr closed; stop watching it so select() can block again
                    self._sel.unregister(key.fileobj)
            elif chunk:
                data += chunk
                if self._first_data is None:
                    self._first_data = time.monotonic()
            else:
                self._on_exit()
                break
        return data

    def _log_stderr(self, chunk: bytes) -> None:
        self._stderr_buf += chunk
        *lines, self._stderr_buf = self._stderr_buf.split(b"\n")
        if not chunk and self._stderr_buf:  # EOF: flush the partial line
            lines, self._stderr_buf = [*lines, self._stderr_buf], b""
        for line in lines:
            if line.strip():
                self._log.warning("ffmpeg: %s", line.decode("utf-8", "replace").rstrip())

    def _on_exit(self) -> None:
        proc = self._proc
        for key in list(self._sel.get_map().values()):
            if key.data == "err":
                rest = proc.stderr.read()
                self._log_stderr(rest)
                self._log_stderr(b"")
            self._sel.unregister(key.fileobj)
        code = proc.wait()
        proc.stdout.close()
        proc.stderr.close()
        self._proc = None
        now = time.monotonic()
        if self._first_data is not None and now - self._first_data >= HEALTHY_AFTER:
            self._backoff = BACKOFF_START
        self._log.warning("ffmpeg exited with code %s; restarting in %.0f s", code, self._backoff)
        self._restart_at = now + self._backoff
        self._backoff = min(self._backoff * 2, BACKOFF_MAX)

    def close(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
            self._proc = None
        self._sel.close()


class UdpSource:
    def __init__(self, url: str) -> None:
        host, port, query = parse_udp_url(url)
        self.restarts = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        if ipaddress.ip_address(host).is_multicast:
            self.sock.bind(("", port))
            iface = socket.inet_aton(query.get("iface", "0.0.0.0"))
            mreq = struct.pack("4s4s", socket.inet_aton(host), iface)
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        else:
            self.sock.bind((host, port))
        self.sock.setblocking(False)
        self._sel = selectors.DefaultSelector()
        self._sel.register(self.sock, selectors.EVENT_READ)

    def read(self, timeout: float) -> bytes:
        if not self._sel.select(timeout):
            return b""
        data = b""
        while True:
            try:
                data += self.sock.recv(READ_SIZE)
            except BlockingIOError:
                return data

    def close(self) -> None:
        self._sel.close()
        self.sock.close()


class StdinSource:
    def __init__(self) -> None:
        self.restarts = 0
        self._fd = sys.stdin.buffer.fileno()
        self._sel = selectors.DefaultSelector()
        self._sel.register(self._fd, selectors.EVENT_READ)

    def read(self, timeout: float) -> bytes:
        if not self._sel.select(timeout):
            return b""
        data = os.read(self._fd, READ_SIZE)
        if not data:
            raise EOFError
        return data

    def close(self) -> None:
        self._sel.close()


def open_source(cfg: SourceConfig, log: logging.Logger) -> Source:
    if cfg.type == "ffmpeg":
        return FfmpegSource(cfg, log)
    if cfg.type == "udp":
        return UdpSource(cfg.url)
    return StdinSource()
