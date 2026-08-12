"""CLI wrapper for the final immutable evidence index."""

from __future__ import annotations

import argparse

from opspilot.evaluation.final_report import build_final_evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--hybrid", required=True)
    parser.add_argument("--reliability", required=True)
    parser.add_argument("--output", default="artifacts/stage3/final_evidence.json")
    args = parser.parse_args()
    print(
        build_final_evidence(
            baseline_dir=args.baseline,
            hybrid_dir=args.hybrid,
            reliability_dir=args.reliability,
            output_path=args.output,
        )
    )


if __name__ == "__main__":
    main()
