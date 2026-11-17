"""python3 -m reference_data checksums | scenes | oracle"""

from __future__ import annotations

import argparse
import json
import sys

from reference_data import generator, oracle, scenes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reference_data", description=__doc__)
    parser.add_argument("what", choices=("checksums", "scenes", "oracle"))
    what = parser.parse_args(argv).what
    if what == "checksums":
        print(json.dumps({"version": generator.VERSION, "seed": generator.SEED, "tables": generator.checksums()}, indent=2))
    elif what == "scenes":
        sys.stdout.write(scenes.scenes_markdown())
    else:
        print(json.dumps({k: str(v) for k, v in oracle.totals().items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
