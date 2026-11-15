import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from labelcast.config import load_config
from labelcast.label import LabelError
from labelcast.watcher import LabelWatcher

LOG = logging.getLogger("test.watcher")
CONF = """[source]
type = "stdin"
[output]
url = "{url}"
[label]
policy = "NATO"
classification = "{cls}"
"""
XML_CONF = '[source]\ntype = "stdin"\n[output]\nurl = "-"\n[label]\nxml_file = "label.xml"\n'
ROOT = Path(__file__).resolve().parents[2]


def _write(path: Path, text: str) -> None:
    """Write and move mtime forward, so a change is visible even on coarse timestamps."""
    old = path.stat().st_mtime_ns if path.exists() else 0
    path.write_text(text)
    os.utime(path, ns=(old + 1_000_000_000, old + 1_000_000_000))


def _make(tmp_path: Path, cls: str = "UNCLASSIFIED", url: str = "-", **kw) -> tuple[Path, LabelWatcher]:
    conf = tmp_path / "label.conf"
    _write(conf, CONF.format(cls=cls, url=url))
    return conf, LabelWatcher(conf, load_config(conf), LOG, **kw)


def test_initial_label(tmp_path):
    _, w = _make(tmp_path)
    assert "UNCLASSIFIED" in w.label.summary and w.interval == 1.0
    assert w.check() is False  # nothing changed


def test_invalid_initial_label_raises(tmp_path):
    conf = tmp_path / "label.conf"
    _write(conf, XML_CONF)  # label.xml missing
    with pytest.raises(LabelError):
        LabelWatcher(conf, load_config(conf), LOG)


def test_valid_change_accepted(tmp_path, caplog):
    conf, w = _make(tmp_path)
    _write(conf, CONF.format(cls="RESTRICTED", url="-"))
    with caplog.at_level(logging.INFO):
        assert w.check() is True
    assert "RESTRICTED" in w.label.summary
    assert "label accepted" in caplog.text


def test_invalid_change_keeps_old(tmp_path, caplog):
    conf, w = _make(tmp_path)
    before = w.label
    _write(conf, CONF.format(cls="RESTRICTED", url="-")[:-10])  # half-written TOML
    assert w.check() is False
    assert w.label == before
    assert "label change rejected" in caplog.text and "TOML" in caplog.text
    caplog.clear()
    assert w.check() is False  # same broken file: no repeated error
    assert "rejected" not in caplog.text


def test_recovers_after_fix(tmp_path):
    conf, w = _make(tmp_path)
    _write(conf, "[label\n")
    assert w.check() is False
    _write(conf, CONF.format(cls="SECRET", url="-"))
    assert w.check() is True and "SECRET" in w.label.summary


def test_source_change_warns_restart(tmp_path, caplog):
    conf, w = _make(tmp_path)
    _write(conf, CONF.format(cls="UNCLASSIFIED", url="udp://127.0.0.1:1234"))
    w.check()
    assert "restart" in caplog.text
    assert "UNCLASSIFIED" in w.label.summary


def test_xml_file_change_detected(tmp_path):
    conf = tmp_path / "label.conf"
    xml = tmp_path / "label.xml"
    _write(xml, (ROOT / "examples" / "label.xml").read_text())
    _write(conf, XML_CONF)
    w = LabelWatcher(conf, load_config(conf), LOG)
    _write(xml, (ROOT / "examples" / "label.xml").read_text().replace("UNCLASSIFIED", "RESTRICTED"))
    assert w.check() is True
    assert b"RESTRICTED" in w.label.payload


def test_rename_save_detected(tmp_path):
    conf, w = _make(tmp_path)
    tmp = tmp_path / "label.conf.tmp"
    tmp.write_text(CONF.format(cls="CONFIDENTIAL", url="-"))
    os.replace(tmp, conf)
    assert w.check() is True and "CONFIDENTIAL" in w.label.summary


def test_missing_file_during_save_is_ignored(tmp_path):
    conf, w = _make(tmp_path)
    conf.unlink()
    assert w.check() is False


def test_request_reload_forces(tmp_path, caplog):
    t = [datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)]
    conf, w = _make(tmp_path, now=lambda: t[0])
    t[0] += timedelta(seconds=1)
    w.request_reload()
    with caplog.at_level(logging.INFO):
        assert w.check() is True
    assert "label accepted" in caplog.text


def test_file_changing_during_read_is_retried(tmp_path, monkeypatch):
    import labelcast.watcher as watcher_mod
    conf, w = _make(tmp_path)
    full = CONF.format(cls="SECRET", url="-") + '[[label.category]]\ntag_name="Releasable to"\ntype="PERMISSIVE"\nvalues=["FIN"]\n'
    _write(conf, CONF.format(cls="SECRET", url="-"))  # valid but incomplete: category not written yet
    real_load = watcher_mod.load_config

    def load_while_writer_finishes(path):
        cfg = real_load(path)
        _write(conf, full)  # the writer completes the file after our read
        return cfg

    monkeypatch.setattr(watcher_mod, "load_config", load_while_writer_finishes)
    assert w.check() is False          # partial read must not be published
    monkeypatch.setattr(watcher_mod, "load_config", real_load)
    assert w.check() is True
    assert b"Releasable to" in w.label.payload


def test_rejected_switch_to_xml_file_recovers(tmp_path):
    conf, w = _make(tmp_path)
    _write(conf, XML_CONF)             # label.xml does not exist yet → rejected
    assert w.check() is False
    xml = tmp_path / "label.xml"
    _write(xml, (ROOT / "examples" / "label.xml").read_text().replace("UNCLASSIFIED", "RESTRICTED"))
    assert w.check() is True
    assert b"RESTRICTED" in w.label.payload
