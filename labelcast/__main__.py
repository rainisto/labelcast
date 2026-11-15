"""labelcast CLI: source → framer → injector → output, with live label reload."""

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from .config import ConfigError, load_config
from .inject import Injector
from .label import LabelError
from .output import open_output
from .source import open_source
from .ts import PacketFramer
from .watcher import LabelWatcher

READ_TIMEOUT = 0.02
WATCH_PERIOD = 1.0
STATS_PERIOD = 10.0
NO_PMT_AFTER = 5.0
NO_PMT_REPEAT = 10.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="labelcast",
                                     description="Bind a STANAG 4774 label to an MPEG-TS stream and send it over UDP.")
    parser.add_argument("-c", "--config", required=True, type=Path, help="path to label.conf (TOML)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(stream=sys.stderr, level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("labelcast")

    try:
        config = load_config(args.config)
        watcher = LabelWatcher(args.config, config, log)
        output = open_output(config.output.url)
        source = open_source(config.source, log)
    except (ConfigError, LabelError, ValueError, OSError) as exc:
        log.error("startup failed: %s", exc)
        return 2

    log.info("label accepted: %s", watcher.label.summary)
    injector = Injector(config.output.label_pid, log=log)
    injector.set_label(watcher.label, watcher.interval)

    stop = False

    def _stop(signum, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGHUP, lambda signum, frame: watcher.request_reload())

    framer = PacketFramer()
    restarts_seen = source.restarts
    now = time.monotonic()
    next_watch = now + WATCH_PERIOD
    next_stats = now + STATS_PERIOD
    first_data: float | None = None
    next_pmt_warning = 0.0
    try:
        while not stop:
            try:
                data = source.read(READ_TIMEOUT)
            except EOFError:
                break
            now = time.monotonic()
            if source.restarts != restarts_seen:
                restarts_seen = source.restarts
                injector.reset()
                framer = PacketFramer()
                first_data = None
            if data:
                if first_data is None:
                    first_data = now
                    next_pmt_warning = now + NO_PMT_AFTER
                for pkt in framer.feed(data):
                    output.write(injector.process(pkt))
            output.tick(now)

            if now >= next_watch:
                next_watch = now + WATCH_PERIOD
                if watcher.check():
                    injector.set_label(watcher.label, watcher.interval)
            if first_data is not None and not injector.pmt_seen and now >= next_pmt_warning:
                log.warning("no PMT found in the input yet; forwarding packets without a label")
                next_pmt_warning = now + NO_PMT_REPEAT
            if now >= next_stats:
                next_stats = now + STATS_PERIOD
                _log_stats(log, injector, framer, source)
    finally:
        output.close()
        source.close()
    _log_stats(log, injector, framer, source)
    return 0


def _log_stats(log: logging.Logger, injector: Injector, framer: PacketFramer, source) -> None:
    s = injector.stats
    log.info("stats: packets in %d out %d, labels %d, dropped %d, resyncs %d, source restarts %d",
             s["packets_in"], s["packets_out"], s["labels"], s["dropped"], framer.resyncs, source.restarts)


if __name__ == "__main__":
    sys.exit(main())
