"""CLI wrapper for the Stage 3 demo evidence gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from opspilot.evaluation.ci_validation import validate_adaptive_demos


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reliability", type=Path, required=True)
    parser.add_argument("--fixed", type=Path, required=True)
    parser.add_argument("--adaptive", type=Path, required=True)
    parser.add_argument("--without-l2", type=Path, required=True)
    parser.add_argument("--full", type=Path, required=True)
    parser.add_argument("--frozen", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(validate_adaptive_demos(
        args.reliability, args.fixed, args.adaptive, args.without_l2, args.full, args.frozen
    ), sort_keys=True))


if __name__ == "__main__":
    main()
