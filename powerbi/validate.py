"""Check the PBIR report files against Microsoft's published JSON schemas.

    python powerbi/validate.py <path to a clone of github.com/microsoft/json-schemas>

Each file names its schema in "$schema"; the URL is mapped onto the clone.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft7Validator
from referencing import Registry, Resource

PREFIX = "https://developer.microsoft.com/json-schemas/"


def main(schemas: Path) -> int:
    def retrieve(uri: str) -> Resource:
        path = schemas / uri.removeprefix(PREFIX)
        return Resource.from_contents(json.loads(path.read_text(encoding="utf-8")))

    registry = Registry(retrieve=retrieve)
    root = Path(__file__).resolve().parent
    files = [p for p in root.rglob("*.json") if "StaticResources" not in p.parts]
    files += list(root.rglob("*.pbir")) + list(root.rglob("*.pbism")) + list(root.rglob("*.pbip"))
    failures = checked = 0
    for path in sorted(files):
        doc = json.loads(path.read_text(encoding="utf-8"))
        uri = doc.get("$schema", "")
        local = schemas / uri.removeprefix(PREFIX)
        if not uri.startswith(PREFIX) or not local.exists():
            print(f"skip (schema not published): {path.relative_to(root)}")
            continue
        schema = json.loads(local.read_text(encoding="utf-8"))
        errors = sorted(Draft7Validator(schema, registry=registry).iter_errors(doc), key=str)
        checked += 1
        for e in errors[:5]:
            failures += 1
            print(f"FAIL {path.relative_to(root)}: {'/'.join(map(str, e.absolute_path))}: {e.message[:300]}")
    print(f"{checked} files checked, {failures} errors")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
