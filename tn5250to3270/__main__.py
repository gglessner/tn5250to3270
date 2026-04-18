import argparse
import logging
import sys
from .config import load_config
from .listener import serve


def main() -> int:
    ap = argparse.ArgumentParser(prog="tn5250to3270",
        description="TN5250↔TN3270 protocol-converting proxy")
    ap.add_argument("-c", "--config", required=True,
                    help="path to config.yaml")
    ap.add_argument("--log-level",
                    help="override log level from config")
    args = ap.parse_args()

    config = load_config(args.config)
    level = args.log_level or config.log_level

    if config.log_format == "json":
        # Minimal JSON formatter — no extra deps
        class JsonFormatter(logging.Formatter):
            def format(self, r):
                import json
                return json.dumps({
                    "ts": self.formatTime(r), "level": r.levelname,
                    "logger": r.name, "msg": r.getMessage(),
                })
        h = logging.StreamHandler()
        h.setFormatter(JsonFormatter())
        logging.basicConfig(level=level, handlers=[h])
    else:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        )

    serve(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
