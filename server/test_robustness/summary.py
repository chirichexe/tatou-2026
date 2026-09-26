"""Print a bench.py result file as a table: ✓ read, · lost, ✗ wrong recipient."""

from __future__ import annotations

import json
import sys

COLUMNS = [("D", "blind", "D"), ("D", "fp", "D.fp"), ("K", "blind", "K"), ("F", "blind", "F"),
           ("G", "combined", "G"), ("G", "D", "G.D"), ("G", "K", "G.K"), ("G", "F", "G.F"),
           ("G", "fp", "G.fp")]


def mark(value: str | None) -> str:
    if value is None:
        return " "
    return "✓" if value.startswith("OK") else "✗" if value.startswith("WRONG") else "·"


def main():
    with open(sys.argv[1], encoding="utf-8") as result_file:
        results = json.load(result_file)["results"]
    cols = [c for c in COLUMNS if any(c[1] in r.get(c[0], {}) for r in results.values())]
    print(f"{'attack':32s} " + " ".join(f"{c[2]:>5s}" for c in cols))
    survived = {c[2]: 0 for c in cols}
    wrong = []
    for attack, r in results.items():
        row = []
        for kind, field, label in cols:
            value = r.get(kind, {}).get(field)
            row.append(mark(value))
            survived[label] += bool(value and value.startswith("OK"))
            if value and value.startswith("WRONG"):
                wrong.append((attack, label))
        print(f"{attack:32s} " + " ".join(f"{x:>5s}" for x in row))
    print(f"{'survived / ' + str(len(results)):32s} " + " ".join(f"{survived[c[2]]:>5d}" for c in cols))
    print("wrong attributions:", wrong or "none")


if __name__ == "__main__":
    main()
