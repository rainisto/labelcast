"""Live reload of the [label] section: poll the config (and xml_file), publish only valid labels."""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .config import Config, ConfigError, load_config
from .label import Label, LabelError, make_label


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LabelWatcher:
    def __init__(self, path: Path, config: Config, log: logging.Logger,
                 now: Callable[[], datetime] = utcnow) -> None:
        self._path = path
        self._initial = config
        self._config = config
        self._log = log
        self._now = now
        self._reload_requested = False
        self.label: Label = make_label(config.label, now())  # LabelError propagates: startup must fail
        self.interval: float = config.label.interval
        self._signature = self._stat()

    def request_reload(self) -> None:
        self._reload_requested = True

    def check(self) -> bool:
        signature = self._stat()
        if signature is None:  # file briefly missing (e.g. during a rename-save)
            return False
        if signature == self._signature and not self._reload_requested:
            return False
        self._signature = signature
        self._reload_requested = False
        try:
            config = load_config(self._path)
            label = make_label(config.label, self._now())
        except (ConfigError, LabelError) as exc:
            self._log.error("label change rejected: %s", exc)
            return False
        if config.source != self._initial.source or config.output != self._initial.output:
            self._log.warning("[source]/[output] changed; restart labelcast to apply (still using old values)")
        self._config = config
        self.label = label
        self.interval = config.label.interval
        self._signature = self._stat() or signature
        self._log.info("label accepted: %s", label.summary)
        return True

    def _stat(self) -> tuple | None:
        paths = [self._path]
        if self._config.label.xml_file is not None:
            paths.append(self._config.label.xml_file)
        sig = []
        for p in paths:
            try:
                st = os.stat(p)
            except OSError:
                if p == self._path:
                    return None
                sig.append(None)
                continue
            sig.append((st.st_mtime_ns, st.st_size, st.st_ino))
        return tuple(sig)
