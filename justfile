# itevi-mlde — root justfile. Delegates to per-container recipes.
#
# Path-scoped shas: a Terraform commit must NOT retag a container.
# See docs/conventions.md §4.

esm2_sha := `git log -1 --format=%h -- containers/esm2-scorer shared/ 2>/dev/null || echo nogit`
region   := env_var_or_default("AWS_REGION", "us-east-2")

default:
    @just --list

doctor:
    ./scripts/doctor.sh

# --- esm2-scorer -------------------------------------------------------------
esm2-build:
    time docker build -f containers/esm2-scorer/Dockerfile \
        --build-arg GIT_SHA={{esm2_sha}} \
        -t itevi-mlde/esm2-scorer:{{esm2_sha}} .

esm2-build-cpu:
    time docker build -f containers/esm2-scorer/Dockerfile.cpu \
        --build-arg GIT_SHA={{esm2_sha}} \
        -t itevi-mlde/esm2-scorer:cpu-{{esm2_sha}} .

# IMAGE_DIGEST is resolved at RUN time — a registry assigns it on push, so it
# cannot be a build arg. Reads "unset" until the first ECR push in Week 2, which
# is correct and honest rather than a stale value baked into a layer.
esm2-score library="library_1024" mode="both":
    #!/usr/bin/env bash
    set -euo pipefail
    IMG=itevi-mlde/esm2-scorer:{{esm2_sha}}
    DIGEST=$(docker inspect --format '{{{{index .RepoDigests 0}}}}' "$IMG" 2>/dev/null || echo unset)
    RUN_ID=$(uuidgen)
    mkdir -p "$ITEVI_SCRATCH/out/esm2/{{library}}"
    docker run --rm --gpus all \
        --user "$(id -u):$(id -g)" --shm-size 2g \
        -e IMAGE_DIGEST="$DIGEST" -e IMAGE_TAG="$IMG" -e RUN_ID="$RUN_ID" \
        -e OMP_NUM_THREADS="${OMP_NUM_THREADS:-16}" \
        -v "$PWD/config:/config:ro" \
        -v "$ITEVI_SCRATCH:/scratch" \
        "$IMG" score \
        --input "/scratch/libraries/{{library}}.parquet" \
        --output "/scratch/out/esm2/{{library}}/$RUN_ID.parquet" \
        --parent /config/parents/itevi.fasta \
        --mode {{mode}} --device cuda --dtype float32

# --- housekeeping ------------------------------------------------------------
disk:
    docker system df
    df -h "$ITEVI_SCRATCH"

prune:
    docker builder prune --filter until=168h -f

shas:
    @echo "esm2-scorer: {{esm2_sha}}"
