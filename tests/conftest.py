import os
import subprocess
from pathlib import Path

import pytest

DATA_DIR = Path(__file__).parent / "data"
READER_IMAGE = "conflabelreader:iptables-2026"


@pytest.fixture
def data_dir() -> Path:
    return DATA_DIR


@pytest.fixture
def docker_cmd() -> list[str]:
    cmd = os.environ.get("DOCKER", "sudo docker").split()
    probe = subprocess.run([*cmd, "image", "inspect", READER_IMAGE],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if probe.returncode != 0:
        pytest.skip(f"docker image {READER_IMAGE} not available")
    return cmd
