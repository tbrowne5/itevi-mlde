#!/usr/bin/env bash
# Verify a host is ready to build and run esm2-scorer.
#
# Runs on macOS or Ubuntu; reports what each host can and cannot do. Run it on
# every new server before you trust it. Exit 0 = ready for its role.

set -uo pipefail

PASS=0; FAIL=0; WARN=0
ok()   { printf '  \033[1;32mPASS\033[0m  %s\n' "$*"; PASS=$((PASS+1)); }
no()   { printf '  \033[1;31mFAIL\033[0m  %s\n' "$*"; FAIL=$((FAIL+1)); }
meh()  { printf '  \033[1;33mWARN\033[0m  %s\n' "$*"; WARN=$((WARN+1)); }
head_() { printf '\n\033[1;34m%s\033[0m\n' "$*"; }

OS=$(uname -s); ARCH=$(uname -m)

head_ "Host"
echo "  $(hostname) -- $OS / $ARCH"

# -------------------------------------------------------------------- role ---
if [[ "$OS" == "Darwin" ]]; then
  ROLE="local (learning + MPS exploration)"
elif [[ "$ARCH" == "x86_64" ]]; then
  ROLE="server (build + GPU runs)"
else
  ROLE="unknown"
fi
echo "  role: $ROLE"

# ------------------------------------------------------------------ docker ---
head_ "Docker"
if command -v docker >/dev/null 2>&1; then
  ok "docker CLI: $(docker --version | cut -d, -f1)"
  if docker info >/dev/null 2>&1; then
    ok "daemon reachable (context: $(docker context show 2>/dev/null))"
    echo "        data-root: $(docker info --format '{{.DockerRootDir}}')"
    if docker buildx version >/dev/null 2>&1; then
      ok "buildx available"
    else
      meh "buildx missing (needed only for cross-arch builds)"
    fi
  else
    no "daemon unreachable -- not running, or user not in the docker group"
  fi
else
  no "docker not installed"
fi

# --------------------------------------------------------------------- gpu ---
head_ "GPU"
if [[ "$OS" == "Darwin" ]]; then
  meh "Apple Silicon: no CUDA. MPS only, and only outside containers."
elif command -v nvidia-smi >/dev/null 2>&1; then
  DRV=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
  MAJOR=${DRV%%.*}
  if (( MAJOR >= 525 )); then
    ok "driver $DRV (CUDA 12.x capable)"
  else
    no "driver $DRV is too old for CUDA 12.x containers (need >= 525)"
  fi
  nvidia-smi --query-gpu=index,name,compute_cap,memory.total,memory.used \
             --format=csv,noheader | sed 's/^/        /'
  BUSY=$(nvidia-smi --query-compute-apps=pid --format=csv,noheader | wc -l | tr -d ' ')
  if [[ "$BUSY" != "0" ]]; then
    meh "$BUSY process(es) already using GPUs -- shared box, check before you queue"
  fi
else
  no "nvidia-smi not found"
fi

# ----------------------------------------------------- container GPU bridge --
head_ "Container GPU passthrough"
if [[ "$OS" == "Darwin" ]]; then
  meh "n/a on macOS"
elif command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  if docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia; then
    ok "nvidia runtime registered with dockerd"
  else
    no "nvidia runtime not registered -- run: sudo nvidia-ctk runtime configure --runtime=docker"
  fi
  if docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 \
       nvidia-smi -L >/dev/null 2>&1; then
    ok "--gpus all works end to end"
  else
    no "--gpus all failed (toolkit missing, or the base image tag has moved)"
  fi
else
  meh "skipped: no reachable daemon"
fi

# ---------------------------------------------------------------- capacity ---
head_ "Capacity"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  DR=$(docker info --format '{{.DockerRootDir}}')
  AV=$(df -BG "$DR" 2>/dev/null | tail -1 | awk '{print $4}' | tr -dc '0-9')
  [[ -z "${AV:-}" ]] && AV=0
  if (( AV >= 60 )); then ok "docker volume: ${AV}G free"
  elif (( AV >= 30 )); then meh "docker volume: ${AV}G free -- tight for image + cache"
  else no "docker volume: ${AV}G free -- will fail mid-build"; fi
fi
echo "        home: $(df -h "$HOME" | tail -1 | awk '{print $4}') free"
echo "        RAM:  $( [[ "$OS" == "Darwin" ]] && echo "$(($(sysctl -n hw.memsize)/1073741824))G" || free -g | awk '/^Mem:/{print $2"G"}')"

# ----------------------------------------------------------------- tooling ---
head_ "Tooling"
for t in git uv just jq aws rsync tmux; do
  if command -v "$t" >/dev/null 2>&1; then ok "$t"; else meh "$t missing"; fi
done

# --------------------------------------------------------------- pytorch -----
head_ "PyTorch (host env, if present)"
if command -v python3 >/dev/null 2>&1; then
  python3 - <<'PY' 2>/dev/null || echo "        torch not installed in this python"
import torch
print(f"        torch {torch.__version__}  cuda={torch.version.cuda}")
print(f"        cuda.is_available={torch.cuda.is_available()}")
if hasattr(torch.backends, "mps"):
    print(f"        mps.is_available={torch.backends.mps.is_available()}")
PY
fi

printf '\n\033[1m%s\033[0m\n' "$PASS pass / $WARN warn / $FAIL fail"
(( FAIL == 0 )) || exit 1
