import subprocess
import sys
from pathlib import Path

from tests.helpers import first_pmt, labels_in, stream_seconds

ROOT = Path(__file__).resolve().parents[2]
XML_EL = bytes.fromhex("050824584d4c34373734260e0104ff24584d4c012f0434373734")
CONF = """[source]
type = "stdin"
[output]
url = "-"
[label]
policy = "NATO"
classification = "UNCLASSIFIED"
"""


def _run(conf: Path, stdin: bytes) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "labelcast", "-c", str(conf)], input=stdin,
                          capture_output=True, cwd=ROOT, timeout=60)


def test_main_stdin_to_stdout(data_dir, tmp_path):
    conf = tmp_path / "label.conf"
    conf.write_text(CONF)
    res = _run(conf, (data_dir / "foreman_cif_klv.ts").read_bytes())
    assert res.returncode == 0, res.stderr.decode()
    assert first_pmt(res.stdout).elements[-1].es_info == XML_EL
    labels = labels_in(res.stdout)
    assert abs(len(labels) - round(stream_seconds(res.stdout))) <= 1
    assert b"label accepted: NATO UNCLASSIFIED xml" in res.stderr


def test_main_bad_config_exit_2(tmp_path):
    conf = tmp_path / "label.conf"
    conf.write_text(CONF.replace('classification = "UNCLASSIFIED"\n', ""))
    res = _run(conf, b"")
    assert res.returncode == 2
    assert b"classification" in res.stderr
    assert res.stdout == b""


def test_main_bad_output_url_exit_2(tmp_path):
    conf = tmp_path / "label.conf"
    conf.write_text(CONF.replace('url = "-"', 'url = "tcp://x:1"'))
    res = _run(conf, b"")
    assert res.returncode == 2
    assert b"udp://" in res.stderr
