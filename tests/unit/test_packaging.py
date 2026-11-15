from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_docker_image_has_no_default_label_config():
    # A missing or misspelled -v mount must fail at startup, not stream video under an example label.
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "/conf" not in "".join(line for line in dockerfile.splitlines() if line.startswith("COPY"))
