"""labelcast TOML configuration model and validation."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

SOURCE_TYPES = ("ffmpeg", "udp", "stdin")
ENCODINGS = ("xml", "brl")
CATEGORY_TYPES = ("PERMISSIVE", "RESTRICTIVE", "INFORMATIVE")
MIN_INTERVAL = 0.1


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class SourceConfig:
    type: str
    args: tuple[str, ...] = ()
    url: str | None = None


@dataclass(frozen=True)
class OutputConfig:
    url: str
    label_pid: int = 0x0102


@dataclass(frozen=True)
class Originator:
    id: str
    type: str


@dataclass(frozen=True)
class Category:
    tag_name: str
    type: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class LabelConfig:
    encoding: str = "xml"
    interval: float = 1.0
    policy: str | None = None
    classification: str | None = None
    originator: Originator | None = None
    categories: tuple[Category, ...] = ()
    xml_file: Path | None = None


@dataclass(frozen=True)
class Config:
    source: SourceConfig
    output: OutputConfig
    label: LabelConfig


def _table(data: dict, key: str) -> dict:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"missing [{key}] section")
    return value


def _str(table: dict, key: str, where: str, required: bool = True) -> str | None:
    value = table.get(key)
    if value is None:
        if required:
            raise ConfigError(f"{where}.{key} is required")
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}.{key} must be a non-empty string")
    return value


def _source(data: dict) -> SourceConfig:
    t = _table(data, "source")
    kind = t.get("type")
    if kind not in SOURCE_TYPES:
        raise ConfigError(f"source.type must be one of {', '.join(SOURCE_TYPES)}")
    args = t.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ConfigError("source.args must be a list of strings")
    if kind == "ffmpeg" and not args:
        raise ConfigError("source.args is required for type ffmpeg")
    url = _str(t, "url", "source", required=(kind == "udp"))
    return SourceConfig(kind, tuple(args), url)


def _output(data: dict) -> OutputConfig:
    t = _table(data, "output")
    url = _str(t, "url", "output")
    pid = t.get("label_pid", 0x0102)
    if not isinstance(pid, int) or not 0x0010 <= pid <= 0x1FFE:
        raise ConfigError("output.label_pid must be an integer 0x0010..0x1FFE")
    return OutputConfig(url, pid)


def _label(data: dict, base_dir: Path) -> LabelConfig:
    t = _table(data, "label")
    encoding = t.get("encoding", "xml")
    if encoding not in ENCODINGS:
        raise ConfigError(f"label.encoding must be one of {', '.join(ENCODINGS)}")
    interval = t.get("interval", 1.0)
    if not isinstance(interval, (int, float)) or isinstance(interval, bool) or interval < MIN_INTERVAL:
        raise ConfigError(f"label.interval must be a number >= {MIN_INTERVAL}")

    field_keys = ("policy", "classification", "originator", "category")
    if "xml_file" in t:
        if any(k in t for k in field_keys):
            raise ConfigError("label.xml_file cannot be combined with label fields "
                              "(policy, classification, originator, category)")
        xml_file = _str(t, "xml_file", "label")
        return LabelConfig(encoding, float(interval), xml_file=base_dir / xml_file)

    originator = None
    if "originator" in t:
        o = t["originator"]
        if not isinstance(o, dict):
            raise ConfigError("label.originator must be a table {id, type}")
        originator = Originator(_str(o, "id", "label.originator"), _str(o, "type", "label.originator"))

    categories = []
    raw_categories = t.get("category", [])
    if not isinstance(raw_categories, list):
        raise ConfigError("label.category must be an array of tables ([[label.category]])")
    for i, c in enumerate(raw_categories):
        where = f"label.category[{i}]"
        if not isinstance(c, dict):
            raise ConfigError(f"{where} must be a table")
        ctype = c.get("type")
        if ctype not in CATEGORY_TYPES:
            raise ConfigError(f"{where}.type must be one of {', '.join(CATEGORY_TYPES)}")
        values = c.get("values")
        if not isinstance(values, list) or not values or not all(isinstance(v, str) and v for v in values):
            raise ConfigError(f"{where}.values must be a non-empty list of strings")
        categories.append(Category(_str(c, "tag_name", where), ctype, tuple(values)))

    return LabelConfig(
        encoding=encoding,
        interval=float(interval),
        policy=_str(t, "policy", "label"),
        classification=_str(t, "classification", "label"),
        originator=originator,
        categories=tuple(categories),
    )


def parse_config(text: str, base_dir: Path) -> Config:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML: {exc}") from exc
    return Config(_source(data), _output(data), _label(data, base_dir))


def load_config(path: Path) -> Config:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    return parse_config(text, path.parent)
