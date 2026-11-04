#!/usr/bin/env python3
"""Pin desktop/ to the package set a haiku.rag release was tested with.

    scripts/pin-haiku-rag.py VERSION UV_LOCK

UV_LOCK is haiku.rag's uv.lock at the release's commit. Rewrites, in
desktop/pyproject.toml, the haiku.rag-slim requirement to VERSION and the
[tool.uv] constraint-dependencies to every package version that lock holds, so
`uv lock --project desktop` resolves what upstream's tests ran on. Run
`uv lock --project desktop` afterwards.
"""

import re
import sys
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parent.parent / "desktop" / "pyproject.toml"
BEGIN = "# BEGIN constraints from haiku.rag's uv.lock"
END = "# END constraints"


def constraints(lock: Path) -> list[str]:
    packages = tomllib.loads(lock.read_text(encoding="utf-8"))["package"]
    pinned = {
        p["name"]: p["version"]
        for p in packages
        if "version" in p and "registry" in p.get("source", {})
    }
    return [f'    "{name}=={version}",' for name, version in sorted(pinned.items())]


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    version, lock = sys.argv[1], Path(sys.argv[2])
    text = PYPROJECT.read_text(encoding="utf-8")
    text = re.sub(r'"haiku\.rag-slim\[([^\]]*)\]==[^"]*"', rf'"haiku.rag-slim[\1]=={version}"', text)
    block = "\n".join([BEGIN, *constraints(lock), "    " + END])
    text = re.sub(rf"{re.escape(BEGIN)}.*?{re.escape(END)}", lambda _: block, text, flags=re.S)
    PYPROJECT.write_text(text, encoding="utf-8")
    print(f"{PYPROJECT}: haiku.rag-slim {version}, constraints from {lock}")


if __name__ == "__main__":
    main()
