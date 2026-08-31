# PyBOP calibration workflow image: pinned Python + PyBaMM + PyBOP + fixed
# thread env vars so parallel calibration runs (e.g. multi-start optimisers)
# are reproducible across machines.
FROM python:3.13-slim

WORKDIR /app

COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# Unset thread counts default to "use all cores", which makes BLAS/OpenMP-backed
# solves (and PyBOP's parallel cost evaluations) non-deterministic in timing
# and, for some reduction orders, in results. Override at `docker run` time
# with `-e OMP_NUM_THREADS=N` etc. if intentionally benchmarking scaling.
ENV OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    PYTHONHASHSEED=0

COPY env_fingerprint.py ./

ENTRYPOINT ["python"]
