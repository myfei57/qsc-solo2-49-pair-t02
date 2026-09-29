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
    log = logging.getLogger("linecontrol")
    log.info("recovered %d snapshots", len(recovery.loaded))
    holds = line.holds()
    if holds:
        log.warning(
            "health check raised %d hold(s); line restricted until confirmed: %s",
            len(holds),
            ", ".join(hold.code for hold in holds),
        )
    app = create_app(line, config.web_root())
    serve(config, app)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
