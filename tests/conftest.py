import os
import subprocess
from pathlib import Path

import pytest

DATA_DIR = Path(__file__).parent / "data"
READER_IMAGE = "conflabelreader:iptables-2026"


@pytest.fixture
def data_dir() -> Path:
    return DATA_DIR


def first_section(path: Path, want_pid: int) -> bytes:
    from labelcast.ts import PACKET_SIZE, SectionAssembler, pid
    raw = path.read_bytes()
    asm = SectionAssembler()
    for i in range(0, len(raw), PACKET_SIZE):
        pkt = raw[i:i + PACKET_SIZE]
        if pid(pkt) == want_pid:
            sections = asm.push(pkt)
            if sections:
                return sections[0]
    raise AssertionError(f"no section on PID {want_pid:#x} in {path.name}")


@pytest.fixture
def xml_sample_pmt_section() -> bytes:
    return first_section(DATA_DIR / "foreman_cif_klv_4774xml.ts", 0x1000)


@pytest.fixture
def exi_sample_pmt():
    from labelcast.ts import parse_pmt
    return parse_pmt(first_section(DATA_DIR / "foreman_cif_klv_4774.ts", 0x1000))


@pytest.fixture
def docker_cmd() -> list[str]:
    cmd = os.environ.get("DOCKER", "sudo docker").split()
    probe = subprocess.run([*cmd, "image", "inspect", READER_IMAGE],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if probe.returncode != 0:
        pytest.skip(f"docker image {READER_IMAGE} not available")
    return cmd
