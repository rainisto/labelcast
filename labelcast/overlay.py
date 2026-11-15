"""Demo visualisation: burn the current label into the video with ffmpeg drawtext.

The overlay only shows what labelcast sends. The binding itself, which receivers use
for filtering, is the label PES in the transport stream.
"""

import os
import xml.etree.ElementTree as ET
from pathlib import Path

FONT_MONO = Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")
FONT_BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


def overlay_text(xml: bytes, mode: str) -> str:
    """'xml': the label document as text; 'banner': one line such as 'NATO SECRET · Releasable to: FIN'."""
    if mode == "xml":
        return xml.decode("utf-8", "replace").replace("\t", "  ")
    root = ET.fromstring(xml)
    policy = root.findtext(".//{*}PolicyIdentifier", "").strip()
    classification = root.findtext(".//{*}Classification", "").strip()
    parts = [" ".join(p for p in (policy, classification) if p)]
    for cat in root.findall(".//{*}Category"):
        values = [v.text.strip() for v in cat.findall("{*}GenericValue") if v.text and v.text.strip()]
        parts.append(f"{cat.get('TagName', '')}: {', '.join(values)}")
    return " · ".join(parts)


class OverlayFile:
    """Text file read by ffmpeg drawtext (reload=1); replaced atomically so ffmpeg never reads half a file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, text: str) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.path)

    def remove(self) -> None:
        self.path.unlink(missing_ok=True)


def drawtext_filter(mode: str, textfile: Path) -> str:
    if mode == "banner":
        font, opts = FONT_BOLD, "fontsize=32:x=(w-text_w)/2:y=12:boxcolor=black@0.7:boxborderw=10"
    else:
        font, opts = FONT_MONO, "fontsize=16:x=10:y=10:boxcolor=black@0.6:boxborderw=8:line_spacing=2"
    fontfile = f"fontfile={font}:" if font.exists() else ""
    return (f"drawtext={fontfile}textfile={textfile}:reload=1:expansion=none:"
            f"fontcolor=white:box=1:{opts}")
