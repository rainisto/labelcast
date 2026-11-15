from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import brotli
import pytest

from labelcast.config import LabelConfig, load_config
from labelcast.label import LabelError, build_label_xml, make_label, validate_label_xml

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
EXAMPLE = load_config(ROOT / "examples" / "label.conf").label


def test_build_matches_spec_example():
    assert build_label_xml(EXAMPLE, NOW) == (ROOT / "examples" / "label.xml").read_bytes()


def test_minimal_fields_only():
    xml = build_label_xml(LabelConfig(policy="NATO", classification="SECRET"), NOW)
    validate_label_xml(xml)
    assert b"<slab:Classification>SECRET</slab:Classification>" in xml
    assert b"OriginatorID" not in xml and b"Category" not in xml


def test_escaping():
    xml = build_label_xml(replace(EXAMPLE, classification='A&B<"C"'), NOW)
    validate_label_xml(xml)
    assert b"A&amp;B&lt;" in xml


def test_attribute_escaping():
    cat = replace(EXAMPLE.categories[0], tag_name='Rel "to" & <x>')
    xml = build_label_xml(replace(EXAMPLE, categories=(cat,)), NOW)
    validate_label_xml(xml)


def test_make_label_xml():
    lab = make_label(EXAMPLE, NOW)
    assert lab.encoding == "xml" and lab.fmt == b"$XML"
    assert lab.payload == build_label_xml(EXAMPLE, NOW)
    assert lab.summary == f"NATO UNCLASSIFIED xml {len(lab.payload)}B"


def test_brl_roundtrip():
    lab = make_label(replace(EXAMPLE, encoding="brl"), NOW)
    assert lab.fmt == b"$BRL"
    assert brotli.decompress(lab.payload) == build_label_xml(EXAMPLE, NOW)
    assert lab.summary.endswith(f"brl {len(lab.payload)}B")


def test_xml_file_mode_sends_bytes_unchanged(tmp_path):
    raw = (ROOT / "examples" / "label.xml").read_bytes().replace(b"UNCLASSIFIED", b"RESTRICTED")
    f = tmp_path / "label.xml"
    f.write_bytes(raw)
    lab = make_label(LabelConfig(xml_file=f), NOW)
    assert lab.payload == raw
    assert lab.summary == f"file label.xml xml {len(raw)}B"


def test_xml_file_wrong_root_rejected(tmp_path):
    f = tmp_path / "l.xml"
    f.write_bytes(b'<?xml version="1.0"?><other xmlns="urn:x"/>')
    with pytest.raises(LabelError, match="originatorConfidentialityLabel"):
        make_label(LabelConfig(xml_file=f), NOW)


def test_xml_file_malformed_rejected(tmp_path):
    f = tmp_path / "l.xml"
    f.write_bytes((ROOT / "examples" / "label.xml").read_bytes()[:200])  # half-written
    with pytest.raises(LabelError, match="well-formed"):
        make_label(LabelConfig(xml_file=f), NOW)


def test_xml_file_missing_rejected(tmp_path):
    with pytest.raises(LabelError, match="cannot read"):
        make_label(LabelConfig(xml_file=tmp_path / "none.xml"), NOW)


def test_payload_too_large_rejected(tmp_path):
    big = ("<v>" + "x" * 70000 + "</v>").encode()
    raw = (ROOT / "examples" / "label.xml").read_bytes().replace(b"</slab:Classification>", b"</slab:Classification>" + big)
    f = tmp_path / "big.xml"
    f.write_bytes(raw)
    with pytest.raises(LabelError, match="65000"):
        make_label(LabelConfig(xml_file=f), NOW)


@pytest.mark.parametrize("enc", ["xml", "brl"])
def test_label_keeps_plain_xml(enc):
    lab = make_label(replace(EXAMPLE, encoding=enc), NOW)
    assert lab.xml == build_label_xml(EXAMPLE, NOW)
