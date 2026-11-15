"""Integration tests: labelcast output decoded by the real ConfLabelReader (and its iptables watchdog).

NEVER run the reader container with --network host: its iptables commands would change the
host's OUTPUT policy. Port publishing (-p) is used instead.
"""

import os
import re
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from tests.conftest import READER_IMAGE
from tests.helpers import stream_seconds

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
CONF = """[source]
{source}
[output]
url = "{url}"
[label]
encoding = "{enc}"
policy = "NATO"
classification = "{cls}"
originator = {{ id = "labelcast-test@example.org", type = "rfc822Name" }}
"""


def _labelcast_stdout(conf: Path, ts: bytes) -> bytes:
    res = subprocess.run([sys.executable, "-m", "labelcast", "-c", str(conf)], input=ts,
                         capture_output=True, cwd=ROOT, timeout=60)
    assert res.returncode == 0, res.stderr.decode()
    return res.stdout


def _decode(docker_cmd: list[str], ts: bytes) -> str:
    res = subprocess.run([*docker_cmd, "run", "--rm", "-i", READER_IMAGE, "ConfLabelReader"],
                         input=ts, capture_output=True, timeout=60)
    return res.stdout.decode("utf-8", "replace")


def _free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _write_conf(path: Path, cls: str, source: str, url: str, enc: str = "xml") -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(CONF.format(source=source, url=url, enc=enc, cls=cls))
    os.replace(tmp, path)


@pytest.mark.parametrize("enc", ["xml", "brl"])
def test_reader_decodes_our_labels(enc, docker_cmd, data_dir, tmp_path):
    conf = tmp_path / "label.conf"
    _write_conf(conf, "UNCLASSIFIED", 'type = "stdin"', "-", enc)
    out = _labelcast_stdout(conf, (data_dir / "foreman_cif_klv.ts").read_bytes())
    text = _decode(docker_cmd, out)
    n = int(re.search(r"Labels read: (\d+)", text).group(1))
    assert abs(n - round(stream_seconds(out))) <= 1
    assert text.count("<slab:Classification>UNCLASSIFIED</slab:Classification>") == n


def test_reader_sees_only_our_label_on_exi_source(docker_cmd, data_dir, tmp_path):
    conf = tmp_path / "label.conf"
    _write_conf(conf, "UNCLASSIFIED", 'type = "stdin"', "-")
    text = _decode(docker_cmd, _labelcast_stdout(conf, (data_dir / "foreman_cif_klv_4774.ts").read_bytes()))
    n = int(re.search(r"Labels read: (\d+)", text).group(1))
    assert n > 0
    assert text.count("labelcast-test@example.org") == n
    assert "jean.guy@forces.gc.ca" not in text


class _LiveSetup:
    """labelcast (ffmpeg looping a sample in real time) → UDP → reader container."""

    def __init__(self, docker_cmd: list[str], data_dir: Path, tmp_path: Path, reader_args: str,
                 cap_net_admin: bool) -> None:
        self.docker = docker_cmd
        self.port = _free_udp_port()
        self.conf = tmp_path / "label.conf"
        sample = data_dir / "foreman_cif_klv.ts"
        self.source = (f'type = "ffmpeg"\nargs = ["-re", "-stream_loop", "-1", "-i", "{sample}", '
                       f'"-map", "0:v", "-c", "copy"]')
        self.url = f"udp://127.0.0.1:{self.port}"
        self.set_classification("UNCLASSIFIED")
        self.name = f"labelcast-it-{uuid.uuid4().hex[:8]}"
        caps = ["--cap-add", "NET_ADMIN"] if cap_net_admin else []
        subprocess.run([*docker_cmd, "run", "-d", "--name", self.name, *caps,
                        "-p", f"{self.port}:{self.port}/udp", READER_IMAGE, "bash", "-c",
                        f"IReflxApp -s0.0.0.0:{self.port} | ConfLabelReader {reader_args}"],
                       check=True, capture_output=True)
        time.sleep(1.5)
        self.proc = subprocess.Popen([sys.executable, "-m", "labelcast", "-c", str(self.conf)], cwd=ROOT,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def set_classification(self, cls: str) -> None:
        _write_conf(self.conf, cls, self.source, self.url)

    def policy(self) -> str:
        res = subprocess.run([*self.docker, "exec", self.name, "iptables-legacy", "-S", "OUTPUT"],
                             capture_output=True, text=True)
        return res.stdout.strip()

    def wait_policy(self, want: str, timeout: float) -> float:
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if self.policy() == want:
                return time.monotonic() - t0
            time.sleep(0.25)
        raise AssertionError(f"policy did not become {want!r} within {timeout} s (now {self.policy()!r})")

    def logs(self) -> str:
        return subprocess.run([*self.docker, "logs", self.name], capture_output=True, text=True).stdout

    def close(self) -> str:
        self.proc.terminate()
        _, err = self.proc.communicate(timeout=10)
        subprocess.run([*self.docker, "rm", "-f", self.name], capture_output=True)
        return err.decode("utf-8", "replace")


def test_live_reload_visible_in_reader(docker_cmd, data_dir, tmp_path):
    live = _LiveSetup(docker_cmd, data_dir, tmp_path, "", cap_net_admin=False)
    try:
        time.sleep(4)
        live.set_classification("RESTRICTED")
        time.sleep(4)
        logs = live.logs()
    finally:
        err = live.close()
    seq = re.findall(r"<slab:Classification>(\w+)</slab:Classification>", logs)
    assert "UNCLASSIFIED" in seq and "RESTRICTED" in seq, err
    switch = seq.index("RESTRICTED")
    assert all(c == "UNCLASSIFIED" for c in seq[:switch])
    assert all(c == "RESTRICTED" for c in seq[switch:])


def test_watchdog_follows_classification(docker_cmd, data_dir, tmp_path):
    live = _LiveSetup(docker_cmd, data_dir, tmp_path, "-rUNCLASSIFIED", cap_net_admin=True)
    try:
        live.wait_policy("-P OUTPUT ACCEPT", 6)
        live.set_classification("RESTRICTED")
        live.wait_policy("-P OUTPUT DROP", 6)
        live.set_classification("UNCLASSIFIED")
        live.wait_policy("-P OUTPUT ACCEPT", 3)
    finally:
        live.close()
