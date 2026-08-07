#!/usr/bin/env bash
# Bootstrap an Ubuntu GPU server for the I-TevI MLDE stack.
#
# Idempotent: safe to re-run. Checks before installing. This script IS the
# server documentation -- when you move to a different Ubuntu box, you run this,
# not a wiki page you half-remember.
#
#   ./bootstrap-ubuntu.sh              # dry run, prints the plan
#   ./bootstrap-ubuntu.sh --apply      # actually do it
#   ./bootstrap-ubuntu.sh --apply --data-root /scratch/docker
#
# Tested against Ubuntu 22.04 and 24.04.

set -euo pipefail

APPLY=0
DATA_ROOT=""
TARGET_USER="${SUDO_USER:-$USER}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --data-root) DATA_ROOT="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m  %s\n' "$*"; }
run()  {
  if [[ $APPLY -eq 1 ]]; then
    eval "$@"
  else
    printf '    would run: %s\n' "$*"
  fi
}

[[ $APPLY -eq 0 ]] && warn "DRY RUN. Re-run with --apply to execute."

# ---------------------------------------------------------------- preflight --
say "Host: $(hostname) / $(. /etc/os-release && echo "$PRETTY_NAME") / $(dpkg --print-architecture)"

if [[ "$(dpkg --print-architecture)" != "amd64" ]]; then
  warn "Not amd64. The Dockerfiles assume x86_64 CUDA images."
fi

# ------------------------------------------------------------ base packages --
say "Base packages"
run "sudo apt-get update -qq"
run "sudo apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg git jq tmux rsync unzip build-essential \
      python3 python3-venv python3-pip pciutils"

# ----------------------------------------------------------- NVIDIA driver ---
say "NVIDIA driver"
if command -v nvidia-smi >/dev/null 2>&1; then
  DRV=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
  say "  driver present: $DRV"
  MAJOR=${DRV%%.*}
  if (( MAJOR < 525 )); then
    warn "  Driver $DRV is below 525. CUDA 12.x containers will not run."
    warn "  Install a newer driver: sudo ubuntu-drivers install"
  elif (( MAJOR < 550 )); then
    say "  OK via CUDA 12.x minor-version compatibility (PyTorch ships its own runtime)."
  else
    say "  OK for CUDA 12.4 natively."
  fi
  nvidia-smi --query-gpu=index,name,compute_cap,memory.total --format=csv || true
else
  warn "  nvidia-smi not found."
  if lspci 2>/dev/null | grep -qi nvidia; then
    warn "  NVIDIA hardware detected but no driver. Installing."
    run "sudo apt-get install -y ubuntu-drivers-common"
    run "sudo ubuntu-drivers install"
    warn "  REBOOT REQUIRED after driver install, then re-run this script."
  else
    warn "  No NVIDIA hardware on this host. CPU-only; GPU steps will be skipped."
  fi
fi

# ------------------------------------------------------------------ Docker ---
say "Docker Engine"
if command -v docker >/dev/null 2>&1; then
  say "  already installed: $(docker --version)"
else
  run "sudo install -m 0755 -d /etc/apt/keyrings"
  run "sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc"
  run "sudo chmod a+r /etc/apt/keyrings/docker.asc"
  run "echo \"deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \$(. /etc/os-release && echo \\\$VERSION_CODENAME) stable\" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null"
  run "sudo apt-get update -qq"
  run "sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin"
fi

say "Docker group membership for $TARGET_USER"
if id -nG "$TARGET_USER" | grep -qw docker; then
  say "  already a member"
else
  run "sudo usermod -aG docker $TARGET_USER"
  warn "  Log out and back in (or: newgrp docker) before docker works without sudo."
fi

# ---------------------------------------------------------- Docker storage ---
# Default /var/lib/docker often sits on a small root partition. One esm2-scorer
# image plus build cache is ~20 GB. This is the single most common way a lab
# server falls over mid-week.
say "Docker storage"
CUR_ROOT=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)
AVAIL=$(df -BG --output=avail "$(dirname "$CUR_ROOT")" 2>/dev/null | tail -1 | tr -dc '0-9' || echo 0)
say "  data-root: $CUR_ROOT  (${AVAIL}G available)"
if (( AVAIL < 60 )); then
  warn "  Under 60 GB free. Move the data-root to a larger volume with --data-root."
fi

if [[ -n "$DATA_ROOT" ]]; then
  say "  Relocating data-root to $DATA_ROOT"
  run "sudo mkdir -p $DATA_ROOT"
  run "sudo systemctl stop docker || true"
  run "sudo mkdir -p /etc/docker"
  run "sudo bash -c 'jq -n --arg dr \"$DATA_ROOT\" \"{\\\"data-root\\\": \\\$dr}\" > /etc/docker/daemon.json'"
  run "sudo rsync -aP /var/lib/docker/ $DATA_ROOT/ || true"
  run "sudo systemctl start docker"
fi

# ------------------------------------------------- NVIDIA Container Toolkit --
# This is the piece that makes `--gpus all` work. Docker alone does not do it.
say "NVIDIA Container Toolkit"
if command -v nvidia-ctk >/dev/null 2>&1; then
  say "  already installed: $(nvidia-ctk --version | head -1)"
else
  if command -v nvidia-smi >/dev/null 2>&1 || [[ $APPLY -eq 0 ]]; then
    run "curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg"
    run "curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list > /dev/null"
    run "sudo apt-get update -qq"
    run "sudo apt-get install -y nvidia-container-toolkit"
    run "sudo nvidia-ctk runtime configure --runtime=docker"
    run "sudo systemctl restart docker"
  else
    warn "  Skipped: no GPU driver on this host."
  fi
fi

# -------------------------------------------------------------- dev tooling --
say "Developer tooling"

if ! command -v uv >/dev/null 2>&1; then
  run "curl -LsSf https://astral.sh/uv/install.sh | sh"
else
  say "  uv: $(uv --version)"
fi

if ! command -v just >/dev/null 2>&1; then
  run "curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh | sudo bash -s -- --to /usr/local/bin"
else
  say "  just: $(just --version)"
fi

if ! command -v aws >/dev/null 2>&1; then
  run "curl -fsSL 'https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip' -o /tmp/awscliv2.zip"
  run "unzip -q -o /tmp/awscliv2.zip -d /tmp && sudo /tmp/aws/install --update"
else
  say "  aws: $(aws --version 2>&1)"
fi

if ! command -v direnv >/dev/null 2>&1; then
  run "sudo apt-get install -y direnv"
fi

# ------------------------------------------------------------------- verify --
say "Done. Verify with:  ./scripts/doctor.sh"
[[ $APPLY -eq 0 ]] && warn "That was a DRY RUN. Nothing changed."
exit 0
