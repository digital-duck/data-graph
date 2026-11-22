"""`python -m datagraf.tool STEP`: one recipe step (a SOLVE node, backend: subprocess) as a program.

stdin   the node's inputs as one JSON object (GraphLang writes it: port `in_x` arrives as key `x`)
stdout  one line of JSON: the step's result (the node's `out_type: JSON` output)

The steps are the public functions of datagraf.steps; each takes keyword arguments named like the node's inputs.
An exception becomes a non-zero exit with the message on stderr, which GraphLang reports as the node's failure.
"""
from __future__ import annotations

import json
import sys


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    from . import steps
    if len(argv) != 1 or argv[0].startswith("_") or not callable(getattr(steps, argv[0], None)):
        names = sorted(n for n in dir(steps) if not n.startswith("_") and callable(getattr(steps, n)) and getattr(getattr(steps, n), "__module__", "") == steps.__name__)
        print(f"usage: python -m datagraf.tool STEP  (one of: {', '.join(names)})", file=sys.stderr)
        return 2
    raw = sys.stdin.read()
    inputs = json.loads(raw) if raw.strip() else {}
    try:
        result = getattr(steps, argv[0])(**inputs)
    except Exception as e:  # noqa: BLE001 - reported to the runtime as the node's failure
        print(f"{argv[0]}: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
