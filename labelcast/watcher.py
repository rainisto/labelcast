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
        # xml_file of the last config that parsed, accepted or not: a rejected switch to
        # xml_file mode must still notice when that file is created or fixed.
        self._watched_xml: Path | None = config.label.xml_file
        self._signature = self._stat()

    def request_reload(self) -> None:
        self._reload_requested = True

    def check(self) -> bool:
        signature = self._stat()
        if signature is None:  # file briefly missing (e.g. during a rename-save)
            return False
        if signature == self._signature and not self._reload_requested:
            return False
        self._reload_requested = False
        try:
            config = load_config(self._path)
        except ConfigError as exc:
            self._reject(exc, signature)
            return False
        self._watched_xml = config.label.xml_file
        before = self._stat()  # now includes the (possibly new) xml_file
        try:
            label = make_label(config.label, self._now())
        except LabelError as exc:
            self._reject(exc, before)
            return False
        after = self._stat()
        if after != before or before[0] != signature[0]:
            # A file changed while we were reading it (in-place save in progress):
            # do not publish what may be a partial read; read again on the next tick.
            self._signature = None
            self._log.debug("config changed during reload; retrying")
            return False
        if config.source != self._initial.source or config.output != self._initial.output:
            self._log.warning("[source]/[output] changed; restart labelcast to apply (still using old values)")
        self._config = config
        self.label = label
        self.interval = config.label.interval
        self._signature = after
        self._log.info("label accepted: %s", label.summary)
        return True

    def _reject(self, exc: Exception, signature: tuple | None) -> None:
        self._log.error("label change rejected: %s", exc)
        self._signature = signature  # report a broken save once, not every second

    def _stat(self) -> tuple | None:
        conf = self._file_sig(self._path)
        if conf is None:
            return None
        xml = self._file_sig(self._watched_xml) if self._watched_xml is not None else None
        return conf, xml

    @staticmethod
    def _file_sig(path: Path) -> tuple | None:
        try:
            st = os.stat(path)
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size, st.st_ino
