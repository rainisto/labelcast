from pathlib import Path

import pytest

from labelcast.config import Category, ConfigError, Originator, load_config, parse_config

ROOT = Path(__file__).resolve().parents[2]
BASE = '[source]\ntype="stdin"\n[output]\nurl="-"\n'


def test_example_config_parses():
    cfg = load_config(ROOT / "examples" / "label.conf")
    assert cfg.source.type == "ffmpeg"
    assert cfg.source.args[:4] == ("-f", "v4l2", "-i", "/dev/video0")
    assert cfg.output.url == "udp://127.0.0.1:50000?ttl=1"
    assert cfg.output.label_pid == 0x0102
    assert cfg.label.encoding == "xml" and cfg.label.interval == 1.0
    assert cfg.label.policy == "NATO" and cfg.label.classification == "UNCLASSIFIED"
    assert cfg.label.originator == Originator("operator@example.org", "rfc822Name")
    assert cfg.label.categories == (Category("Releasable to", "PERMISSIVE", ("FIN", "SWE")),)
    assert cfg.label.xml_file is None


def test_defaults():
    cfg = parse_config(BASE + '[label]\npolicy="NATO"\nclassification="X"', Path("."))
    assert cfg.label.encoding == "xml"
    assert cfg.label.interval == 1.0
    assert cfg.output.label_pid == 0x0102
    assert cfg.label.originator is None and cfg.label.categories == ()


def test_xml_file_resolved_relative_to_config(tmp_path):
    cfg = parse_config(BASE + '[label]\nxml_file="sub/l.xml"', tmp_path)
    assert cfg.label.xml_file == tmp_path / "sub" / "l.xml"
    assert cfg.label.policy is None


def test_udp_source_requires_url():
    with pytest.raises(ConfigError, match="url"):
        parse_config('[source]\ntype="udp"\n[output]\nurl="-"\n[label]\npolicy="N"\nclassification="X"', Path("."))


@pytest.mark.parametrize("snippet, msg", [
    ('[label]\nxml_file="a.xml"\npolicy="NATO"\nclassification="X"', "xml_file"),
    ('[label]\npolicy="NATO"', "classification"),
    ('[label]\nclassification="X"', "policy"),
    ('[label]\npolicy="NATO"\nclassification="X"\ninterval=0.05', "interval"),
    ('[label]\npolicy="NATO"\nclassification="X"\nencoding="exi"', "encoding"),
    ('[label]\npolicy="NATO"\nclassification="X"\n[[label.category]]\ntag_name="t"\ntype="BOGUS"\nvalues=["A"]', "type"),
    ('[label]\npolicy="NATO"\nclassification="X"\n[[label.category]]\ntype="PERMISSIVE"\nvalues=["A"]', "tag_name"),
    ('[label]\npolicy="NATO"\nclassification="X"\noriginator={id="a"}', "originator"),
    ('[label]\npolicy="NATO"\nclassification="X', "TOML"),
])
def test_invalid_configs(snippet, msg):
    with pytest.raises(ConfigError, match=msg):
        parse_config(BASE + snippet, Path("."))


def test_invalid_source_type():
    with pytest.raises(ConfigError, match="source.type"):
        parse_config('[source]\ntype="rtsp"\n[output]\nurl="-"\n[label]\npolicy="N"\nclassification="X"', Path("."))


def test_missing_label_section():
    with pytest.raises(ConfigError, match=r"\[label\]"):
        parse_config(BASE, Path("."))


def test_load_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "nope.conf")
