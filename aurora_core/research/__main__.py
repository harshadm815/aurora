"""
Run, check and report reproducible experiments.

    python -m aurora_core.research run    --spec aurora_core/research/specs/architecture_v1.json --data Data/pg1661.txt
    python -m aurora_core.research verify --spec … --data … --run "T0 | seed 0"
    python -m aurora_core.research report --spec … --data …
    python -m aurora_core.research claims

Results go to docs/research/<experiment name>/ (spec.json, environment.json,
runs.jsonl, results.json, report.md, verifications.jsonl), which are small
and kept in git; trained weights are not kept, only their SHA-256.
"""

import argparse
import json
import sys
from pathlib import Path

from aurora_core.research.claims import write_claims
from aurora_core.research.runner import REPOSITORY, Experiment, SpecError, load_spec, write_outputs

DEFAULT_ROOT = REPOSITORY / "docs" / "research"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aurora_core.research", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "verify", "report"):
        command = sub.add_parser(name)
        command.add_argument("--spec", required=True, type=Path)
        command.add_argument("--data", required=True, type=Path)
        command.add_argument("--out", type=Path, help="results folder (default: docs/research/<name>)")
        if name == "run":
            command.add_argument("--fresh", action="store_true", help="discard recorded runs and start again")
            command.add_argument("--limit", type=int, help="run at most this many new runs, then stop")
        if name == "verify":
            command.add_argument("--run", required=True, help='e.g. "T0 | seed 0"')
    claims = sub.add_parser("claims")
    claims.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    options = parser.parse_args(argv)

    if options.command == "claims":
        path = write_claims(options.root)
        print(f"Written {path}")
        return 0

    try:
        spec = load_spec(options.spec)
        experiment = Experiment(spec, options.data, options.out or DEFAULT_ROOT / spec["name"])
        if options.command == "run":
            total = len(experiment.plan())

            def show(record):
                print(f"{len(experiment.recorded()):3}/{total}  {record['id']:24} best validation "
                      f"{record['best_validation_loss']:.4f}  test {record['test_loss']:.4f}  {record['seconds']:.0f} s",
                      flush=True)

            experiment.run(fresh=options.fresh, on_run=show, limit=options.limit)
            analysis = write_outputs(experiment)
            for variant in analysis["variants"]:
                print(f"{variant['name']:12} {variant.get('verdict', 'baseline')}")
        elif options.command == "verify":
            result = experiment.verify(options.run)
            write_outputs(experiment)
            print(json.dumps(result, indent=2))
            return 0 if result["identical_weights"] else 1
        else:
            write_outputs(experiment)
        print(f"Report: {experiment.out / 'report.md'}")
        return 0
    except SpecError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
