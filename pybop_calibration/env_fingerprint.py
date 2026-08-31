"""Reproducibility fingerprint for PyBOP/PyBaMM calibration runs.

Collects a snapshot of the environment (OS/CPU, Python, package versions,
solver backend, git state, and thread/seed env vars) so it can be archived
alongside a calibration run's results. Calibration scripts should call
`save_fingerprint(output_dir)` once per run.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

# Packages whose versions matter for reproducing calibration numerics.
# pybammsolvers bundles the compiled IDAKLU/SUNDIALS binaries (no separate
# "sundials" package is published), and casadi backs the CasADi solver.
_TRACKED_PACKAGES = [
    "pybop",
    "pybamm",
    "pybammsolvers",
    "casadi",
    "numpy",
    "scipy",
]

# Thread/determinism-sensitive env vars that commonly cause cross-machine drift.
_TRACKED_ENV_VARS = [
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "PYTHONHASHSEED",
]


def _package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name in _TRACKED_PACKAGES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def _all_installed_packages() -> dict[str, str]:
    return {dist.name: dist.version for dist in metadata.distributions() if dist.name}


def _git_info(cwd: Path) -> dict[str, str | bool | None]:
    def run(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
            )
            return result.stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None

    commit = run("rev-parse", "HEAD")
    if commit is None:
        return {"commit": None, "dirty": None}
    status = run("status", "--porcelain")
    return {"commit": commit, "dirty": bool(status)}


def collect_fingerprint() -> dict:
    """Build a JSON-serializable reproducibility snapshot."""
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
        },
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
        },
        "solver_backend": _package_versions(),
        "installed_packages": _all_installed_packages(),
        "git": _git_info(Path(__file__).resolve().parent),
        "env_vars": {name: os.environ.get(name) for name in _TRACKED_ENV_VARS},
    }


def save_fingerprint(output_dir: str | Path, filename: str = "env_fingerprint.json") -> Path:
    """Write the fingerprint snapshot into `output_dir` and return its path."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / filename
    out_path.write_text(json.dumps(collect_fingerprint(), indent=2), encoding="utf-8")
    return out_path


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Directory to save env_fingerprint.json into (default: print to stdout only)",
    )
    args = parser.parse_args()

    fingerprint = collect_fingerprint()
    if args.out is not None:
        path = save_fingerprint(args.out)
        print(f"Saved fingerprint to {path}")
    print(json.dumps(fingerprint, indent=2))


if __name__ == "__main__":
    main()
