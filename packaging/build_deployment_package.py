"""Builds a deployment-ready zip of the full application for the IIS/QA server.

Unlike build_base_project.py (which strips the app down to a generic
template), this script packages the real, current application -- all
tests, utils, and config -- while excluding local dev artifacts that
should never be copied to the server (see DEPLOYMENT_IIS.md).

Usage:
    python packaging/build_deployment_package.py [--version LABEL]
Output:
    dist/cimpl-api-automation-deploy[-LABEL].zip
"""

from __future__ import annotations

import argparse
import fnmatch
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "dist"

# Directories excluded entirely (matched against any path component).
EXCLUDE_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".vscode",
    "dist",
    "packaging",
    ".mypy_cache",
    ".ruff_cache",
}

# Glob patterns excluded regardless of location.
EXCLUDE_FILE_PATTERNS = [
    ".env",
    "*.pyc",
    "*.pyo",
    "validation_output.txt",
    "_tmp_*",
    "*.log",
]

# Paths (relative to ROOT) whose contents are excluded but the folder
# itself (with a .gitkeep-style placeholder) should still exist on the server.
EXCLUDE_CONTENTS_DIRS = {
    "reports",
}


def _is_excluded(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    parts = rel.parts
    if any(part in EXCLUDE_DIR_NAMES for part in parts):
        return True
    if parts and parts[0] in EXCLUDE_CONTENTS_DIRS and len(parts) > 1:
        return True
    if any(fnmatch.fnmatch(path.name, pattern) for pattern in EXCLUDE_FILE_PATTERNS):
        return True
    return False


def _iter_files():
    for path in ROOT.rglob("*"):
        if path.is_file() and not _is_excluded(path):
            yield path


def build(version: str | None) -> Path:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    stamp = version or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    zip_path = DIST_DIR / f"cimpl-api-automation-deploy-{stamp}.zip"
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in _iter_files():
            archive.write(file_path, file_path.relative_to(ROOT))
        # Keep the reports directory present but empty on the server.
        archive.writestr("reports/.gitkeep", "")
        archive.writestr("reports/pdf/.gitkeep", "")

    return zip_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="Label to embed in the zip filename, e.g. a git tag or date.")
    args = parser.parse_args()

    zip_path = build(args.version)
    print(f"Deployment package created: {zip_path}")
    print("Copy this zip to the server, extract it into the app directory, and follow DEPLOYMENT_IIS.md")
    print("(create .env manually on the server -- it is intentionally excluded from this package).")


if __name__ == "__main__":
    main()
