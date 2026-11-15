import logging
import socket
import time

from labelcast.config import SourceConfig
from labelcast.source import FfmpegSource, UdpSource, open_source

LOG = logging.getLogger("test.source")


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_udp_source_receives():
    port = _free_port()
    src = UdpSource(f"udp://127.0.0.1:{port}")
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx.sendto(b"a" * 1316, ("127.0.0.1", port))
    tx.sendto(b"b" * 188, ("127.0.0.1", port))
    time.sleep(0.05)
    assert src.read(0.5) == b"a" * 1316 + b"b" * 188
    assert src.read(0.05) == b""
    src.close()


def test_open_source_kinds():
    port = _free_port()
    src = open_source(SourceConfig("udp", url=f"udp://127.0.0.1:{port}"), LOG)
    assert isinstance(src, UdpSource)
    src.close()


def test_ffmpeg_source_restarts_with_backoff(caplog):
    fake = ["python3", "-c", "import sys; sys.stdout.buffer.write(b'G'*188); sys.stderr.write('bye\\n')"]
    src = FfmpegSource(SourceConfig("ffmpeg", args=("-i", "x")), LOG, cmd=fake)
    t0 = time.monotonic()
    data = b""
    when = {}
    while time.monotonic() - t0 < 4.5 and src.restarts < 2:
        data += src.read(0.05)
        when.setdefault(src.restarts, time.monotonic() - t0)
    src.close()
    assert src.restarts == 2
    assert 0.9 <= when[1] < 2.0       # first restart after 1 s
    assert 2.8 <= when[2] < 4.0       # second after a further 2 s
    assert data.startswith(b"G" * 188)
    assert "bye" in caplog.text       # ffmpeg stderr is forwarded to the log


def test_ffmpeg_closed_stderr_does_not_busy_loop():
    fake = ["python3", "-c", "import os, time; os.close(2); time.sleep(1.5)"]
    src = FfmpegSource(SourceConfig("ffmpeg", args=()), LOG, cmd=fake)
    t0 = time.monotonic()
    for _ in range(5):
        src.read(0.1)
    elapsed = time.monotonic() - t0
    src.close()
    assert elapsed >= 0.4  # each read waited for its timeout instead of returning at once


def test_ffmpeg_command_line():
    src = FfmpegSource(SourceConfig("ffmpeg", args=("-i", "/dev/video0")), LOG, start=False)
    assert src.command == ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-i", "/dev/video0",
                           "-f", "mpegts", "-"]


def test_ffmpeg_stall_is_restarted(caplog):
    fake = ["python3", "-c", "import sys, time; sys.stdout.buffer.write(b'G'*188); sys.stdout.flush(); time.sleep(30)"]
    src = FfmpegSource(SourceConfig("ffmpeg", args=()), LOG, cmd=fake, stall_timeout=1.0)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 4.0 and src.restarts < 1:
        src.read(0.05)
    src.close()
    assert src.restarts == 1
    assert "no data" in caplog.text
