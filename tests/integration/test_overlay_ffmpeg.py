"""Overlay with a real ffmpeg (lavfi test source, no camera, no Docker needed)."""

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.helpers import labels_in

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
CONF = """[source]
type = "ffmpeg"
args = ["-re", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25", "-t", "3",
        "-c:v", "libx264", "-preset", "ultrafast", "-g", "25"]
overlay = "{overlay}"
[output]
url = "-"
[label]
policy = "NATO"
classification = "{cls}"
"""


def _write_conf(path: Path, overlay: str, cls: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(CONF.format(overlay=overlay, cls=cls))
    os.replace(tmp, path)


def _start(tmp_path: Path, overlay: str):
    conf = tmp_path / "label.conf"
    _write_conf(conf, overlay, "UNCLASSIFIED")
    err = open(tmp_path / "err.log", "wb")
    out = open(tmp_path / "out.ts", "wb")
    proc = subprocess.Popen([sys.executable, "-m", "labelcast", "-c", str(conf)], cwd=ROOT,
                            stdout=out, stderr=err, stdin=subprocess.DEVNULL)
    return conf, proc


def _stop(proc) -> None:
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=10)


def _top_left_pixels(ts: Path) -> bytes:
    """Gray pixels of the top-left 300x40 region at t=1 s."""
    return subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", "1", "-i", str(ts),
                           "-frames:v", "1", "-vf", "crop=300:40:0:0,format=gray", "-f", "rawvideo", "-"],
                          capture_output=True, check=True).stdout


def _run_for(work: Path, overlay: str, seconds: float) -> Path:
    work.mkdir()
    _, proc = _start(work, overlay)
    time.sleep(seconds)
    _stop(proc)
    return work / "out.ts"


@pytest.mark.parametrize("mode", ["xml", "banner"])
def test_overlay_is_burned_into_video(mode, tmp_path):
    plain = _run_for(tmp_path / "off", "off", 2.5)
    marked = _run_for(tmp_path / mode, mode, 2.5)
    assert labels_in(marked.read_bytes()), "labels still flow with the overlay on"
    err = (tmp_path / mode / "err.log").read_text()
    assert "Error" not in err and "Invalid" not in err, err
    assert _top_left_pixels(plain) != _top_left_pixels(marked)


def test_overlay_follows_label_change(tmp_path):
    conf, proc = _start(tmp_path, "banner")
    try:
        time.sleep(1.5)
        log = (tmp_path / "err.log").read_text()
        path = Path(re.search(r"overlay banner: (\S+)", log).group(1))
        assert path.read_text() == "NATO UNCLASSIFIED"
        _write_conf(conf, "banner", "SECRET")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and path.read_text() != "NATO SECRET":
            time.sleep(0.1)
        assert path.read_text() == "NATO SECRET"
    finally:
        _stop(proc)
    assert not path.exists(), "overlay file is removed on exit"
