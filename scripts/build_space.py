"""Assemble the Hugging Face Space in one folder: the app from space/, plus the evaluator, the
policy files and the examples exactly as they are in this repo.

    python scripts/build_space.py build/space
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FILES = {
    "space/app.py": "app.py",
    "space/demo.py": "demo.py",
    "space/README.md": "README.md",
    "tools/__init__.py": "tools/__init__.py",
    "tools/policy_eval.py": "tools/policy_eval.py",
}
FOLDERS = {"policies": "*.json", "examples": "*.json"}


def build(out: Path) -> list[str]:
    if out.exists():
        shutil.rmtree(out)
    copied = []
    for source, target in FILES.items():
        (out / target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / source, out / target)
        copied.append(target)
    for folder, pattern in FOLDERS.items():
        for path in sorted((ROOT / folder).rglob(pattern)):
            target = path.relative_to(ROOT)
            (out / target).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, out / target)
            copied.append(str(target))
    return copied


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "build/space")
    files = build(out)
    print(f"{len(files)} files in {out}")
