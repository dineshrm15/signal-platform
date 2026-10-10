"""Print `name==minimum` pins (requirements-file format) for every runtime dependency
declared with `>=`.

CI installs these to test against the oldest versions the packages claim to support.
`uv --resolution lowest` can't do this: it also lowers transitive dependencies to
versions too old to build. Where packages disagree, the highest minimum wins, since
that is what a user installing everything would get.
"""

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFESTS = sorted(ROOT.glob("libs/*/pyproject.toml")) + sorted(
    ROOT.glob("services/*/pyproject.toml")
)
_FLOOR = re.compile(r"^\s*([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*[^;]*?>=\s*([0-9][^,;\s]*)")


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", version))


def main() -> None:
    local = {tomllib.loads(m.read_text())["project"]["name"] for m in MANIFESTS}
    floors: dict[str, str] = {}
    for manifest in MANIFESTS:
        for req in tomllib.loads(manifest.read_text())["project"].get("dependencies", []):
            match = _FLOOR.match(req)
            if not match:
                sys.exit(f"{manifest}: dependency without a >= minimum: {req!r}")
            name, version = match.group(1).lower(), match.group(2)
            if name in local:
                continue
            if name not in floors or _key(version) > _key(floors[name]):
                floors[name] = version
    print("\n".join(f"{name}=={version}" for name, version in sorted(floors.items())))


if __name__ == "__main__":
    main()
