# PyBOP calibration environment

Isolated environment for battery-model calibration with [PyBOP](https://github.com/pybop-team/PyBOP)
on top of this repo's PyBaMM models. Kept separate from the hardware modules
(`ni6212/`, `pcan/`, `logic2/`, `battery_simulator/`), which are Windows/USB-hardware
only and not meant to run in a container.

## Solver backend (recorded per task 1.1)

Locked via `requirements.lock` on 2026-08-26, Python 3.13.15, Windows:

| package | version | role |
|---|---|---|
| pybamm | 26.8.0.0 | model definitions, simulation |
| pybammsolvers | 0.9.1 | bundles the IDAKLU/SUNDIALS solver bindings (no separate `sundials` package is published on PyPI) |
| casadi | 3.7.2 | CasADi solver backend |
| pybop | 26.3 | calibration/optimisation |

`env_fingerprint.py` records these same package versions automatically for
every run so drift between machines/releases is caught after the fact.

## Regenerating the lock file

```powershell
python -m pip install pip-tools
python -m piptools compile --generate-hashes --output-file=requirements.lock requirements.in
```

Re-run this whenever `requirements.in` changes, and update the version table
above if `pybamm`/`pybop`/`pybammsolvers`/`casadi` versions move.

## Conda-forge alternative

`environment.yml` uses conda-forge's `pybamm-base` (core only, no extras;
swap to `pybamm` for the full-extras package) plus `pybop` via the `pip:`
subsection, since PyBOP has no conda-forge package.

```powershell
conda env create -f environment.yml
```

## Docker

Build/run docs only — the image has not been built/verified in this
Windows dev environment; confirm Docker Desktop availability before using.

```powershell
docker build -t pybop-calibration -f Dockerfile .
docker run --rm -v ${PWD}\output:/app/output pybop-calibration env_fingerprint.py --out /app/output
```

Thread env vars (`OMP_NUM_THREADS` etc.) are fixed to `1` in the image for
reproducible parallel calibration runs; override with `docker run -e
OMP_NUM_THREADS=N ...` if intentionally testing scaling behavior.

## Fingerprinting a calibration run

No calibration entry-point script exists yet in this repo. Once one is
added, it should call:

```python
from env_fingerprint import save_fingerprint
save_fingerprint(run_output_dir)  # writes env_fingerprint.json next to results
```

so every run's results directory carries its own reproducibility snapshot.
