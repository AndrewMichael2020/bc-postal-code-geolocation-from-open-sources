#!/usr/bin/env python3
"""Run the greenfield BC postal-code geolocation workflow."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--skip-import", action="store_true")
    parser.add_argument("--download-osm-pbf", action="store_true")
    parser.add_argument("--skip-google", action="store_true")
    parser.add_argument("--execute-google", action="store_true")
    parser.add_argument("--stable-qa-limit", type=int, default=1000)
    parser.add_argument("--refresh-boundaries", action="store_true")
    return parser.parse_args()


def run(command: list[str]) -> None:
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    args = parse_args()
    python = sys.executable
    run_id_args = ["--run-id", args.run_id] if args.run_id else []

    if not args.skip_import:
        command = [python, str(SCRIPTS / "import_postal_sources.py"), *run_id_args]
        if args.download_osm_pbf:
            command.append("--download-osm-pbf")
        run(command)

    run([python, str(SCRIPTS / "compare_postal_sources.py")])

    if not args.skip_google:
        command = [
            python,
            str(SCRIPTS / "google_maps_adjudicate_postal_codes.py"),
            "--stable-qa-limit",
            str(args.stable_qa_limit),
        ]
        if args.execute_google:
            command.append("--execute")
        run(command)

    run([python, str(SCRIPTS / "build_golden_postal_geolocation.py"), *run_id_args])

    health_command = [python, str(SCRIPTS / "enrich_golden_health_authority.py"), *run_id_args]
    if args.refresh_boundaries:
        health_command.append("--refresh-boundaries")
    run(health_command)

    audit_command = [python, str(SCRIPTS / "audit_greenfield_coordinate_rules.py"), *run_id_args]
    if args.refresh_boundaries:
        audit_command.append("--refresh-boundaries")
    run(audit_command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
