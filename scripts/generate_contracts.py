from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.contract_codegen import stale_outputs, write_outputs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    if arguments.check:
        stale = stale_outputs()
        if stale:
            print("stale generated contract artifacts:")
            for path in stale:
                print(path.relative_to(REPOSITORY_ROOT))
            return 1
        return 0
    write_outputs()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
