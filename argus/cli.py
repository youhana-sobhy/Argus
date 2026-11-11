"""Command-line entry point: ``argus serve`` / ``argus export`` / ``argus reset-lab``."""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="argus", description="Argus network automation platform")
    sub = p.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve", help="run the web platform")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--reload", action="store_true")
    ex = sub.add_parser("export", help="print the target config as JSON")
    ex.add_argument("--format", choices=["normalized", "apic", "native"], default="normalized")
    sub.add_parser("reset-lab", help="restore the mock lab to its seed state")
    args = p.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("argus.main:app", host=args.host, port=args.port, reload=args.reload)
        return 0

    from argus.connectors import aci_mo
    from argus.core.inventory import get_inventory

    inv = get_inventory()
    if args.cmd == "export":
        snap = inv.snapshot()
        data = {"normalized": lambda: snap.model_dump(mode="json"), "apic": lambda: aci_mo.export_apic(snap),
                "native": inv.native}[args.format]()
        json.dump(data, sys.stdout, indent=2)
        print()
    elif args.cmd == "reset-lab":
        reset = getattr(inv.connector, "reset", None)
        if reset is None:
            print("Only the mock lab can be reset", file=sys.stderr)
            return 1
        reset()
        print("Mock lab restored to seed state.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
