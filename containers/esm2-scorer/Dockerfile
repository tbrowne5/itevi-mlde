# BUILD CONTEXT IS THE REPO ROOT, not this directory:
#   docker build -f containers/esm2-scorer/Dockerfile -t <tag> .
# The context must include shared/ and config/. See docs/conventions.md.
# FRIDAY ARTIFACT. Multi-stage, cache-ordered, weights baked.
#
# Layer order is deliberate: slowest-and-most-stable first, fastest-and-most-
# volatile last. Your source code changes twenty times a day; torch does not.
#
# NOTE: verify these NVIDIA tags still exist on Docker Hub before building --
# NVIDIA renames cudnn tags between CUDA minors (cudnn8-devel -> cudnn-devel).

# ---------- builder ----------
FROM nvidia/cuda:12.4.1-cudnn-devel-ubuntu22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-dev build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 1. venv + heavy deps. Stable layer, cached across virtually every rebuild.
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY containers/esm2-scorer/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        --extra-index-url https://download.pytorch.org/whl/cu124 \
        -r /tmp/requirements.txt

# 2. Weights, fetched by URL rather than through the esm loader so this layer
#    does not depend on the python environment at all. 2.5 GB you download once.
#    Verify these URLs; fair-esm resolves them from dl.fbaipublicfiles.com.
ENV TORCH_HOME=/opt/torch
RUN mkdir -p /opt/torch/hub/checkpoints \
    && curl -fsSL -o /opt/torch/hub/checkpoints/esm2_t33_650M_UR50D.pt \
        https://dl.fbaipublicfiles.com/fair-esm/models/esm2_t33_650M_UR50D.pt \
    && curl -fsSL -o /opt/torch/hub/checkpoints/esm2_t33_650M_UR50D-contact-regression.pt \
        https://dl.fbaipublicfiles.com/fair-esm/regression/esm2_t33_650M_UR50D-contact-regression.pt \
    && sha256sum /opt/torch/hub/checkpoints/*.pt > /opt/torch/CHECKSUMS

# 3. Source. Volatile layer, last.
# shared first: it changes less often than the container's own source
COPY shared /src/shared
RUN pip install --no-cache-dir --no-deps /src/shared

COPY containers/esm2-scorer/pyproject.toml /src/pyproject.toml
COPY containers/esm2-scorer/src /src/src
RUN pip install --no-cache-dir --no-deps /src

# ---------- runtime ----------
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04 AS runtime

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/torch /opt/torch

ENV PATH="/opt/venv/bin:$PATH" \
    TORCH_HOME=/opt/torch \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Build-time provenance, surfaced into every output row.
ARG GIT_SHA=unset
ENV GIT_SHA=$GIT_SHA
# IMAGE_DIGEST is deliberately NOT a build arg -- the registry assigns it on
# push, so at build time there is nothing to bake. Injected with -e at run time.

COPY config /config

RUN useradd -m -u 1000 scorer
USER scorer
WORKDIR /work

ENTRYPOINT ["esm2-score"]
CMD ["--help"]
