from datetime import datetime, timezone
from pathlib import Path

from labelcast.config import Category, LabelConfig, load_config
from labelcast.label import build_label_xml
from labelcast.overlay import OverlayFile, drawtext_filter, overlay_text

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
EXAMPLE_XML = build_label_xml(load_config(ROOT / "examples" / "label.conf").label, NOW)


def test_xml_mode_is_the_label_document():
    text = overlay_text(EXAMPLE_XML, "xml")
    assert text == EXAMPLE_XML.decode("utf-8")
    assert "\t" not in text


def test_xml_mode_expands_tabs():
    assert overlay_text(b"<a>\n\t<b/>\n</a>", "xml") == "<a>\n  <b/>\n</a>"


def test_banner_mode_summarises_label():
    assert overlay_text(EXAMPLE_XML, "banner") == "NATO UNCLASSIFIED · Releasable to: FIN, SWE"


def test_banner_without_categories():
    xml = build_label_xml(LabelConfig(policy="NATO", classification="SECRET"), NOW)
    assert overlay_text(xml, "banner") == "NATO SECRET"


def test_banner_several_categories():
    cfg = LabelConfig(policy="NATO", classification="RESTRICTED", categories=(
        Category("Releasable to", "PERMISSIVE", ("FIN",)), Category("Mission", "RESTRICTIVE", ("ALPHA", "BRAVO"))))
    assert overlay_text(build_label_xml(cfg, NOW), "banner") == \
        "NATO RESTRICTED · Releasable to: FIN · Mission: ALPHA, BRAVO"


def test_banner_from_raw_xml_file_document():
    xml = (ROOT / "examples" / "label.xml").read_bytes()
    assert overlay_text(xml, "banner") == "NATO UNCLASSIFIED · Releasable to: FIN, SWE"


def test_overlay_file_atomic_write(tmp_path):
    f = OverlayFile(tmp_path / "overlay.txt")
    f.write("first")
    f.write("second")
    assert (tmp_path / "overlay.txt").read_text() == "second"
    assert [p.name for p in tmp_path.iterdir()] == ["overlay.txt"]
    f.remove()
    assert not (tmp_path / "overlay.txt").exists()


def test_drawtext_filter_reloads_file_without_expansion(tmp_path):
    vf = drawtext_filter("xml", tmp_path / "o.txt")
    assert vf.startswith("drawtext=")
    assert f"textfile={tmp_path / 'o.txt'}" in vf
    assert "reload=1" in vf and "expansion=none" in vf and "box=1" in vf


def test_drawtext_banner_is_centered_and_larger(tmp_path):
    xml_vf = drawtext_filter("xml", tmp_path / "o.txt")
    banner_vf = drawtext_filter("banner", tmp_path / "o.txt")
    assert "x=(w-text_w)/2" in banner_vf
    size = lambda vf: int(vf.split("fontsize=")[1].split(":")[0])
    assert size(banner_vf) > size(xml_vf)
