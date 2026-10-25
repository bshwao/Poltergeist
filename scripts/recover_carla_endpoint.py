"""Inspect an explicitly restarted CARLA endpoint before clearing its quarantine."""

import argparse

from simulator.carla_client import CarlaConnectionSettings, connect
from simulator.carla_lease import CarlaEndpointLease


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--confirm-server-restarted", action="store_true", required=True,
                        help="Confirm that you restarted this server after inspecting unfinished work")
    args = parser.parse_args(argv)
    settings = CarlaConnectionSettings(host=args.host, port=args.port)
    lease = CarlaEndpointLease(settings)
    lease.acquire()
    try:
        world = connect(settings).get_world()
        lease.recover_inspected(world)
    finally:
        lease.release()
    print("Empty asynchronous CARLA endpoint inspected; unfinished-episode quarantine cleared.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
