"""STANAG 4774 label document: generation, validation and payload encoding."""

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from xml.sax.saxutils import escape, quoteattr

import brotli

from .config import LabelConfig

NAMESPACE = "urn:nato:stanag:4774:confidentialitymetadatalabel:1:0"
ROOT_TAG = f"{{{NAMESPACE}}}originatorConfidentialityLabel"
MAX_PAYLOAD = 65000
FORMATS = {"xml": b"$XML", "brl": b"$BRL"}


class LabelError(Exception):
    pass


@dataclass(frozen=True)
class Label:
    encoding: str
    fmt: bytes
    payload: bytes
    summary: str


def build_label_xml(cfg: LabelConfig, created: datetime) -> bytes:
    """Byte-stable 4774 document in the layout of the upstream sample (prefix 'slab')."""
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<slab:originatorConfidentialityLabel xmlns:slab="{NAMESPACE}">',
        "  <slab:ConfidentialityInformation>",
        f"    <slab:PolicyIdentifier>{escape(cfg.policy)}</slab:PolicyIdentifier>",
        f"    <slab:Classification>{escape(cfg.classification)}</slab:Classification>",
    ]
    for cat in cfg.categories:
        lines.append(f"    <slab:Category Type={quoteattr(cat.type)} TagName={quoteattr(cat.tag_name)}>")
        lines += [f"      <slab:GenericValue>{escape(v)}</slab:GenericValue>" for v in cat.values]
        lines.append("    </slab:Category>")
    lines.append("  </slab:ConfidentialityInformation>")
    if cfg.originator is not None:
        lines.append(f"  <slab:OriginatorID IDType={quoteattr(cfg.originator.type)}>"
                     f"{escape(cfg.originator.id)}</slab:OriginatorID>")
    lines.append(f"  <slab:CreationDateTime>{created.strftime('%Y-%m-%dT%H:%M:%SZ')}</slab:CreationDateTime>")
    lines.append("</slab:originatorConfidentialityLabel>")
    return ("\n".join(lines) + "\n").encode("utf-8")


def validate_label_xml(xml: bytes) -> None:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise LabelError(f"label XML is not well-formed: {exc}") from exc
    if root.tag != ROOT_TAG:
        raise LabelError(f"label root element must be originatorConfidentialityLabel in namespace "
                         f"{NAMESPACE}, got {root.tag}")


def make_label(cfg: LabelConfig, now: datetime) -> Label:
    if cfg.xml_file is not None:
        try:
            xml = cfg.xml_file.read_bytes()
        except OSError as exc:
            raise LabelError(f"cannot read label file {cfg.xml_file}: {exc}") from exc
        what = f"file {cfg.xml_file.name}"
    else:
        xml = build_label_xml(cfg, now)
        what = f"{cfg.policy} {cfg.classification}"
    validate_label_xml(xml)
    payload = brotli.compress(xml) if cfg.encoding == "brl" else xml
    if len(payload) > MAX_PAYLOAD:
        raise LabelError(f"label payload is {len(payload)} bytes, limit is {MAX_PAYLOAD}")
    return Label(cfg.encoding, FORMATS[cfg.encoding], payload, f"{what} {cfg.encoding} {len(payload)}B")
