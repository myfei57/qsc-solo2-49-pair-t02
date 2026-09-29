"""Service entry point."""

from __future__ import annotations

import logging
import sys

from .config import parse_args
from .console.app import create_app
from .console.server import serve
from .line.machine import Line


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = parse_args(argv)
    try:
        config.host_port()
    except ValueError as error:
        print(f"linecontrol: {error}", file=sys.stderr)
        return 2

    line = Line(config.data_root())
    recovery = line.recover()
    logger = logging.getLogger("linecontrol")
    if line.health.restricted():
        logger.warning(
            "line RESTRICTED: %d blocking item(s) await field confirmation",
            len(line.health.blocking_pending()),
        )
        for item in line.health.pending():
            logger.warning(
                "  [%s] %s: %s (expected %s, actual %s)",
                item.severity.value,
                item.id,
                item.detail,
                item.expected,
                item.actual,
            )
    else:
        logger.info(
            "recovered %d snapshots, no blocking items", len(recovery.loaded)
        )
    app = create_app(line, config.web_root())
    serve(config, app)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
