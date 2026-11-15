"""TS output: UDP datagrams of 7 packets (unicast/multicast) or stdout."""

import socket
import sys
import time
from typing import Callable, Protocol
from urllib.parse import parse_qs, urlsplit

from .ts import PACKET_SIZE

PACKETS_PER_DATAGRAM = 7
FLUSH_AFTER = 0.020


class Output(Protocol):
    def write(self, pkts: list[bytes]) -> None: ...
    def tick(self, now: float) -> None: ...
    def close(self) -> None: ...


def parse_udp_url(url: str) -> tuple[str, int, dict[str, str]]:
    parts = urlsplit(url)
    if parts.scheme != "udp":
        raise ValueError(f"unsupported URL scheme in {url!r} (expected udp://host:port)")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"invalid port in {url!r}") from exc
    if not parts.hostname or port is None:
        raise ValueError(f"URL {url!r} must be udp://host:port")
    query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
    return parts.hostname, port, query


class UdpOutput:
    def __init__(self, url: str, clock: Callable[[], float] = time.monotonic) -> None:
        host, port, query = parse_udp_url(url)
        self._dest = (host, port)
        self._clock = clock
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, int(query.get("ttl", 1)))
        if "iface" in query:
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(query["iface"]))
        self._buf: list[bytes] = []
        self._buf_since = 0.0

    def write(self, pkts: list[bytes]) -> None:
        for pkt in pkts:
            if not self._buf:
                self._buf_since = self._clock()
            self._buf.append(pkt)
            if len(self._buf) == PACKETS_PER_DATAGRAM:
                self._flush()

    def tick(self, now: float) -> None:
        if self._buf and now - self._buf_since >= FLUSH_AFTER:
            self._flush()

    def close(self) -> None:
        if self._buf:
            self._flush()
        self.sock.close()

    def _flush(self) -> None:
        data = b"".join(self._buf)
        self._buf = []
        try:
            self.sock.sendto(data, self._dest)
        except OSError:
            pass  # e.g. ECONNREFUSED / no route: UDP is best effort, keep streaming


class StdoutOutput:
    def __init__(self) -> None:
        self._out = sys.stdout.buffer

    def write(self, pkts: list[bytes]) -> None:
        if pkts:
            self._out.write(b"".join(pkts))
            self._out.flush()

    def tick(self, now: float) -> None:
        pass

    def close(self) -> None:
        self._out.flush()


def open_output(url: str) -> Output:
    if url == "-":
        return StdoutOutput()
    return UdpOutput(url)


assert PACKETS_PER_DATAGRAM * PACKET_SIZE == 1316
