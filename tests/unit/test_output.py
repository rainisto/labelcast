import socket

import pytest

from labelcast.output import StdoutOutput, UdpOutput, open_output

PKT = b"\x47" + b"\x00" * 187


def _receiver() -> tuple[socket.socket, int]:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.settimeout(1.0)
    return s, s.getsockname()[1]


def test_udp_output_datagram_sizes():
    rx, port = _receiver()
    t = [100.0]
    out = UdpOutput(f"udp://127.0.0.1:{port}", clock=lambda: t[0])
    out.write([PKT] * 15)
    assert [len(rx.recv(4096)), len(rx.recv(4096))] == [1316, 1316]
    out.tick(100.01)  # 10 ms: too early
    rx.settimeout(0.1)
    with pytest.raises(socket.timeout):
        rx.recv(4096)
    out.tick(100.03)
    assert len(rx.recv(4096)) == 188
    out.close()


def test_udp_output_close_flushes():
    rx, port = _receiver()
    out = UdpOutput(f"udp://127.0.0.1:{port}")
    out.write([PKT] * 3)
    out.close()
    assert len(rx.recv(4096)) == 3 * 188


def test_udp_output_query_parsing():
    out = UdpOutput("udp://239.1.1.1:5000?ttl=3")
    assert out.sock.getsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL) == 3
    out.close()


def test_udp_output_default_ttl():
    out = UdpOutput("udp://239.1.1.1:5000")
    assert out.sock.getsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL) == 1
    out.close()


@pytest.mark.parametrize("url", ["udp://127.0.0.1", "tcp://127.0.0.1:5", "udp://:5000"])
def test_bad_output_url(url):
    with pytest.raises(ValueError):
        open_output(url)


def test_open_output_kinds():
    assert isinstance(open_output("-"), StdoutOutput)
    out = open_output("udp://127.0.0.1:5000")
    assert isinstance(out, UdpOutput)
    out.close()


def test_stdout_output(capfdbinary):
    out = StdoutOutput()
    out.write([PKT, PKT])
    out.close()
    assert capfdbinary.readouterr().out == PKT * 2
