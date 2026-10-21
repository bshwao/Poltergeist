"""CLI for shared installation diagnostics; no automatic execution or installs."""

import argparse
import json
from pathlib import Path

from backend.diagnostics import collect, carla_probe


def positive(value):
    import math
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= 30:
        raise argparse.ArgumentTypeError("must be finite, above zero and at most 30 seconds")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend", action="store_true", help="Require a supported Node runtime")
    parser.add_argument("--carla", action="store_true", help="Read versions/map/settings from an already running server")
    parser.add_argument("--carla-python", type=Path, help="Interpreter containing the optional CARLA client")
    parser.add_argument("--timeout", type=positive, default=5.)
    parser.add_argument("--hardware", action="store_true", help="Include OS/CPU and optional device-wide GPU snapshot")
    args = parser.parse_args(argv)
    if args.carla_python and not args.carla:
        parser.error("--carla-python requires --carla")
    report = collect(frontend=args.frontend, carla=args.carla, carla_python=args.carla_python,
                     timeout=args.timeout, hardware=args.hardware)
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
