# RUNBOOK — Week 1: server build-out and the esm2-scorer container

Executable companion to `docs/environments.md` (the *why*) and
`docs/conventions.md` (the *naming*). Every command, in order, with a
verification gate after each phase.

**Part A (Phases 1–10)** stands the server up. Run once per machine.
**Part B (Phases 11–18)** is the Week 1 container work, mapped to the plan's
Tue–Fri concept blocks.

**Do not skip a gate.** Each sits where a silent failure would otherwise surface
hours later disguised as something else.

Two things to know before the first command:

- **The repo is `itevi-mlde`, a monorepo.** `esm2-scorer` is one of five Layer 1
  containers inside it, not a repo of its own. See `docs/conventions.md` §1.
- **Docker builds run from the repo root**, not from the container directory:
  `docker build -f containers/esm2-scorer/Dockerfile .` — the context has to
  include `shared/` and `config/`.

---

## Target host

Recorded from your report; Phase 1 confirms each independently.

| | Value | Consequence |
|---|---|---|
| OS | Ubuntu 22.04 (jammy) | apt codename `jammy`; cgroups v2 by default |
| CPU | 112 threads | almost certainly dual-socket → NUMA (Phase 1.3) |
| RAM | 1 TB | memory limits are a non-issue; CPU image is viable for real work |
| GPU | RTX A4000, 16 GB | Ampere, compute capability **8.6** |
| Driver | **570.133.07** | ≥ 550. **No driver work needed — skip §4.2 entirely** |

Three host-specific decisions follow from that table, each handled below:

1. **Driver is already good.** 570 exposes CUDA 12.8; our 12.4 container runtime
   is backward-compatible with it. Phase 4 (driver) does not exist in this
   runbook. Do not run `ubuntu-drivers install` — it can only make things worse.
2. **16 GB VRAM, not 24.** The batch size in the `justfile` was written for a
   24 GB card. Phase 16 measures the real ceiling instead of guessing.
3. **112 threads is a trap for PyTorch.** Left alone, torch spawns 112 OpenMP
   threads for a single-process job and spends more time in synchronisation and
   cross-NUMA memory traffic than in compute. Phase 8 pins this.

---

# Part A — server build-out

Run once per machine. Phases 1–10.

---

## Phase 1 — Discovery (changes nothing)

Run all of it, read all of it, before touching the machine.

### 1.1 Identity and kernel

```bash
hostnamectl
. /etc/os-release && echo "codename=$VERSION_CODENAME"     # expect: jammy
uname -r
dpkg --print-architecture                                   # expect: amd64
stat -fc %T /sys/fs/cgroup                                  # expect: cgroup2fs
```

**GATE 1.1** — `codename=jammy` and `amd64`. If the codename is anything else the
Docker apt line in Phase 3 must change to match.

### 1.2 GPU

```bash
nvidia-smi
nvidia-smi --query-gpu=name,driver_version,compute_cap,memory.total,memory.used,persistence_mode --format=csv
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
nvidia-smi -q | grep -A3 -i "ecc mode"
```

**GATE 1.2** — driver ≥ 525 (yours is 570 ✓), compute_cap `8.6`, and you have
noted whether anyone else's process is currently resident. This is likely a
shared lab machine; a 40-minute job that OOMs someone else's training run is a
bad first week.

If ECC is enabled you lose ~6% of the 16 GB (≈15 GB usable). Note which, because
it changes the batch-size headroom in Phase 9. Leave the setting alone.

### 1.3 CPU topology

```bash
lscpu | grep -E 'Model name|^CPU\(s\)|Thread|Core|Socket|NUMA'
sudo apt-get install -y numactl && numactl --hardware
```

**GATE 1.3** — record the NUMA node count. With 112 threads you almost certainly
have 2 nodes. If `numactl --hardware` shows 2+ nodes, Phase 8's thread pinning is
mandatory, not optional: a torch process spanning both sockets pays a large
penalty fetching weights across the interconnect.

### 1.4 Storage — the decision that matters most

```bash
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT,TYPE
df -hT
findmnt -t nfs,nfs4,cifs        # anything listed here CANNOT host the data root
```

You are looking for a mount with **≥ 100 GB free** on **ext4 or xfs**, on
**local disk**. Candidates in order of likelihood: `/scratch`, `/data`, `/opt`,
`/var`.

For an xfs candidate, one extra check:

```bash
xfs_info /your/candidate | grep ftype     # must be ftype=1
```

**GATE 1.4** — you have chosen a path. Export it now; every later phase uses it:

```bash
export DOCKER_DATA_ROOT=/scratch/docker      # <-- edit to your real choice
export ITEVI_SCRATCH=/scratch/$USER/itevi    # <-- and this
echo "$DOCKER_DATA_ROOT  $ITEVI_SCRATCH"
```

**Stop here if the only large volume is NFS.** overlay2 does not work on NFS
(§4.5). You need local disk for Docker even if your data lives on the network
mount. Find some, or the rest of the runbook will fail in a confusing way.

### 1.5 Existing state

```bash
which docker nvidia-ctk uv just aws jq tmux direnv rsync 2>/dev/null
id -nG                     # are you already in the docker group?
id -u                      # your UID — matters in Phase 11
```

**GATE 1.5** — if `docker` already exists, skip Phase 3 and go straight to
Phase 5. Note your UID; if it is not 1000, the `--user` flag in Phase 11 is
required, not optional.

---

## Phase 2 — Repos (there are two)

### 2.1 The project monorepo

```bash
mkdir -p ~/src && cd ~/src
git clone <your-org>/itevi-mlde.git
cd itevi-mlde
chmod +x scripts/*.sh
```

Confirm the layout is what the rest of this runbook assumes:

```bash
ls -d containers/esm2-scorer shared config docs scripts
git log -1 --format=%h -- containers/esm2-scorer shared/   # the path-scoped tag
```

That second command is your image tag. It moves only when the container or its
shared dependency changes — a Terraform commit must not retag a scorer
(`conventions.md` §4).

### 2.2 The notes vault

```bash
cd ~/src
git clone <your-org>/itevi-notes.git
```

An Obsidian vault with git on — a notes folder, not a second codebase. Open it as
a vault on the **Mac**; on the server it exists only so the `note` shell function
can append to today's entry. Setup is in its own README and takes five minutes.

Wire the secret scanner into **both** repos:

```bash
cd ~/src/itevi-mlde
pip install --user pre-commit
cat > .pre-commit-config.yaml <<'PRECOMMIT'
repos:
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.18.4
    hooks: [{id: gitleaks}]
PRECOMMIT
pre-commit install && pre-commit run --all-files
```

### 2.3 Baseline

```bash
cd ~/src/itevi-mlde
./scripts/doctor.sh || true      # expect FAILs — this is the "before" picture
```

**GATE 2** — both repos cloned, `pre-commit` installed in `itevi-mlde`, and you
have saved `doctor.sh`'s failing output. The diff against Phase 11 is your evidence
the setup worked.

---

## Phase 3 — Docker Engine (§4.3)

Skip if Phase 1.5 found Docker already installed.

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg

sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
     -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
                        docker-buildx-plugin docker-compose-plugin
```

Verify:

```bash
docker --version
docker buildx version
sudo systemctl is-enabled docker && sudo systemctl is-active docker
sudo docker run --rm hello-world
```

**GATE 3** — `hello-world` prints its message under `sudo`. Not yet without.

If you had `docker.io` from Ubuntu's repos installed previously, remove it first
(`sudo apt-get remove docker.io docker-doc podman-docker containerd runc`) — the
two packages conflict and the older BuildKit will misbehave during Wednesday's
cache work.

---

## Phase 4 — Docker group (§4.4)

**Read §4.4 before running this.** Membership in `docker` is equivalent to root
on this host. On a shared lab machine that is a real decision.

```bash
sudo usermod -aG docker "$USER"
newgrp docker            # applies to THIS shell only
```

Verify:

```bash
id -nG | tr ' ' '\n' | grep -x docker
docker run --rm hello-world      # no sudo
```

**GATE 4** — `hello-world` works without `sudo`. If not, log out completely and
back in; group membership is evaluated at login.

---

## Phase 5 — Relocate the data root (§4.5)

Budget on this host: ~60 GB comfortable, and with 1 TB of RAM you are likely on a
machine with generous disk, so give it 200 GB if you have it. Check what you have
now:

```bash
docker info --format 'current data-root: {{.DockerRootDir}}'
df -h "$(docker info --format '{{.DockerRootDir}}')"
```

If the current root has under ~100 GB free, relocate:

```bash
sudo mkdir -p "$DOCKER_DATA_ROOT"
sudo systemctl stop docker docker.socket
sudo mkdir -p /etc/docker

# preserve any existing daemon.json content rather than clobbering it
sudo test -f /etc/docker/daemon.json \
  && sudo cp /etc/docker/daemon.json /etc/docker/daemon.json.bak \
  || echo '{}' | sudo tee /etc/docker/daemon.json > /dev/null

sudo jq --arg dr "$DOCKER_DATA_ROOT" '. + {"data-root": $dr}' \
     /etc/docker/daemon.json | sudo tee /etc/docker/daemon.json.new > /dev/null
sudo mv /etc/docker/daemon.json.new /etc/docker/daemon.json
cat /etc/docker/daemon.json

sudo rsync -aHAX --info=progress2 /var/lib/docker/ "$DOCKER_DATA_ROOT"/
sudo systemctl start docker
```

Verify, then reclaim the old location only once you are satisfied:

```bash
docker info --format '{{.DockerRootDir}}'      # must be your new path
docker run --rm hello-world
df -h "$DOCKER_DATA_ROOT"
# only after the above all pass:
# sudo rm -rf /var/lib/docker
```

**GATE 5** — `DockerRootDir` is your chosen path, `hello-world` still works, and
≥ 100 GB is free there.

---

## Phase 6 — NVIDIA Container Toolkit (§4.6)

This is the step that makes `--gpus` mean anything. Docker alone has no concept
of a GPU.

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list > /dev/null

sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Verify, in this order — each step localises the failure:

```bash
nvidia-ctk --version
docker info --format '{{json .Runtimes}}' | jq .           # must contain "nvidia"
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi -L
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

**GATE 6 — the most important gate in this document.** The last command must
print your A4000 and driver 570.133.07 *from inside a container*. Do not proceed
until it does; every subsequent failure will otherwise be blamed on your
Dockerfile.

If `docker info` shows the nvidia runtime but `--gpus all` fails with
`manifest unknown`, the base image tag has moved — check Docker Hub for the
current `nvidia/cuda` tags and substitute. If it fails with
`could not select device driver`, the daemon did not pick up the config: restart
it again and re-check.

---

## Phase 7 — GPU tuning

### 7.1 Persistence mode

Without it, the driver tears down and re-initialises GPU state between processes,
adding seconds to every container start. You will start containers hundreds of
times this week.

```bash
sudo nvidia-smi -pm 1
systemctl status nvidia-persistenced --no-pager || \
  echo "nvidia-persistenced not present; -pm 1 will not survive reboot"
```

If `nvidia-persistenced` is absent, install it so the setting is durable:

```bash
sudo apt-get install -y nvidia-persistenced
sudo systemctl enable --now nvidia-persistenced
```

**GATE 7.1** — `persistence_mode` reads `Enabled`:

```bash
nvidia-smi --query-gpu=persistence_mode --format=csv
```

### 7.2 TF32 — the reproducibility item

Nothing to configure on the host; this is recorded here because it is
host-*dependent* and your A4000 is exactly the class of card where it bites.

Ampere (cc 8.6) enables cuDNN's TF32 path by default, truncating matmul mantissas
from 23 bits to 10. That is invisible for training and harmless for ranking, but
it puts a ~1e-3 floor on host-to-host numerical agreement — and the Week 10
pipeline test checks against ~1e-6.

`scoring.load_model()` now sets both `torch.backends.cuda.matmul.allow_tf32` and
`torch.backends.cudnn.allow_tf32` to `False`, and `allow_tf32` is written into
every output row. Confirm after Phase 10:

```bash
python -c "import pandas as pd; d=pd.read_parquet('out/esm2_scores_1024.parquet'); \
print(d[['allow_tf32','gpu_name','dtype','torch_version']].iloc[0])"
```

**GATE 7.2** — `allow_tf32` is `False` in the output. If two hosts ever disagree
numerically, this column is the first place to look.

---

## Phase 8 — Tooling and thread policy

### 8.1 Install

```bash
sudo apt-get install -y jq tmux rsync unzip direnv build-essential \
                        python3 python3-venv python3-pip pciutils hmmer

curl -LsSf https://astral.sh/uv/install.sh | sh
curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh \
  | sudo bash -s -- --to /usr/local/bin

curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
unzip -q -o /tmp/awscliv2.zip -d /tmp && sudo /tmp/aws/install --update

echo 'eval "$(direnv hook bash)"' >> ~/.bashrc && source ~/.bashrc
```

(`hmmer` is included because Week 0's jackhmmer GIY-YIG MSA build is blocking
Week 2, and on 112 threads this machine will chew through it. See 8.3.)

Verify:

```bash
for t in docker uv just jq aws rsync tmux direnv; do
  printf '%-8s %s\n' "$t" "$(command -v $t || echo MISSING)"
done
aws --version         # must be 2.x, not 1.x
```

**GATE 8.1** — all present, AWS CLI reports `aws-cli/2.x`.

### 8.2 Thread policy — do not skip this on a 112-thread box

By default PyTorch sets `torch.get_num_threads()` to the core count. For a
single-process inference job on a dual-socket machine, 112 OpenMP threads means
most of the wall time goes to barrier synchronisation and cross-NUMA memory
traffic. Measured throughput usually *drops* past ~16 threads for this workload.

```bash
cat >> .envrc <<'EOF'

# 112-thread dual-socket host: cap torch's OpenMP pool. See RUNBOOK Phase 8.2.
export OMP_NUM_THREADS=16
export MKL_NUM_THREADS=16
export OPENBLAS_NUM_THREADS=16
EOF
direnv allow
```

For containers, pass it explicitly (`Dockerfile.cpu` already sets a default):

```bash
docker run --rm -e OMP_NUM_THREADS=16 --cpus 16 ...
```

Measure rather than trust the number — one masked-marginal pass at three settings:

```bash
for n in 8 16 32 112; do
  echo "== OMP_NUM_THREADS=$n"
  OMP_NUM_THREADS=$n python -c "
import time, torch, esm
torch.set_num_threads($n)
m,a = esm.pretrained.esm2_t33_650M_UR50D(); m.eval()
_,_,t = a.get_batch_converter()([('x','M'*245)])
t0=time.time()
with torch.inference_mode():
    for _ in range(5): m(t)
print(f'{(time.time()-t0)/5:.2f} s/pass')"
done
```

**GATE 8.2** — you have a measured optimum. Put that number in `.envrc` and in
`learning-log.md`. If 112 wins, great — but you will almost certainly find it
does not, and knowing *why* is worth the ten minutes.

### 8.3 Bonus: the blocking MSA build

Week 2's `evmutation-scorer` blocks on the GIY-YIG MSA, which Week 0 flagged as
long-running. This machine makes it cheap. Start it in tmux now and forget it:

```bash
tmux new -s msa
# jackhmmer -N 5 --cpu 96 -A giy_yig.sto --incT <threshold> itevi.fasta uniref90.fasta
# Ctrl-b d
```

Being a week ahead on the one item that gates Week 2 is worth an hour today.

---

## Phase 9 — Filesystem layout (§4.8)

```bash
mkdir -p "$ITEVI_SCRATCH"/{out,ref}
ln -sfn "$ITEVI_SCRATCH/out" ~/src/itevi-mlde/out
ln -sfn "$ITEVI_SCRATCH/ref" ~/src/itevi-mlde/ref
ls -la ~/src/itevi-mlde/ | grep -E 'out|ref'
df -h "$ITEVI_SCRATCH"
```

**GATE 9** — `out/` and `ref/` are symlinks onto scratch. Code lives in the repo,
data lives on scratch, nothing that matters lives in `$HOME`.

Confirm `.gitignore` covers `out/` and `ref/` so a stray `git add -A` cannot
commit a 2 GB Parquet.

---

## Phase 10 — Mac side and SSH (§6)

On the **Mac**:

```bash
cd ~/src/itevi-mlde
./scripts/setup-mac.sh
```

`~/.ssh/config` on the Mac:

```
Host itevi-gpu
    HostName <server>
    User <you>
    IdentityFile ~/.ssh/id_ed25519
    ServerAliveInterval 30
    ServerAliveCountMax 6
    ControlMaster auto
    ControlPath ~/.ssh/cm-%r@%h:%p
    ControlPersist 10m
    Compression no
```

Deliberately **no `ForwardAgent`** — read §6.2 and decide. If you want git push
from the server, the contained option is a server-side deploy key:

```bash
# on the server
ssh-keygen -t ed25519 -C "itevi-gpu deploy" -f ~/.ssh/id_ed25519_deploy
cat ~/.ssh/id_ed25519_deploy.pub   # add to GitHub as a repo deploy key (write)
```

Verify multiplexing is working — the second connect should be near-instant:

```bash
time ssh itevi-gpu true
time ssh itevi-gpu true
ssh -O check itevi-gpu       # "Master running (pid=...)"
```

**GATE 10** — second connect is under ~200 ms, and VS Code Remote-SSH opens
`~/src/itevi-mlde` on the server.

---

# Part B — Week 1: the esm2-scorer container

Phases 11–18 map onto the plan's Tue–Fri hybrid-Docker blocks: each concept is
introduced, then immediately applied to the real container. Mon Aug 3 is the
Civic Holiday.

| Phase | Day | Concept block | Applied to |
|---|---|---|---|
| 11 | Tue | images/layers, build context, `.dockerignore` | `hello-py`, then the repo's context |
| 12 | Tue | — | clear the two blockers (parent seq, `library.yaml`) |
| 13 | Wed | build cache and layer ordering | `Dockerfile.naive`, measured |
| 14 | Wed | — | fix the cache defects, re-measure |
| 15 | Thu | GPU containers, `--gpus`, driver injection | build the CUDA image, smoke test |
| 16 | Thu | — | batch-size calibration for 16 GB |
| 17 | Fri | multi-stage builds, image size | shrink it, run the full library |
| 18 | Fri | — | cross-checks and sign-off |

---

## Phase 11 — Tue: fundamentals, on a toy then on the real repo

### 11.1 The 30-minute warm-up

```bash
mkdir -p /tmp/hello-py && cd /tmp/hello-py
printf 'print("hello from a container")\n' > app.py
printf 'FROM python:3.11-slim\nWORKDIR /app\nCOPY app.py .\nCMD ["python", "app.py"]\n' > Dockerfile

docker build -t hello-py .
docker run --rm hello-py
docker images hello-py
docker history hello-py            # <-- the point of the exercise
docker run --rm -it hello-py bash  # look around; note what is NOT here
```

`docker history` is what turns "an image is a stack of layers" from a phrase into
something concrete. Every `RUN`/`COPY`/`ADD` is one row.

### 11.2 Build context — see it, then fix it

```bash
dd if=/dev/zero of=junk.bin bs=1M count=2000
docker build -t hello-py .          # watch "transferring context: 2.0GB"

echo "junk.bin" > .dockerignore
docker build -t hello-py .          # watch it vanish

cd ~ && rm -rf /tmp/hello-py
```

Ten minutes, and you will never again wonder why a build hangs *before* it starts.
This matters more than usual here: in the monorepo the build context is the whole
repo root, because the image needs `shared/` and `config/`.

### 11.3 Check the repo's own context

```bash
cd ~/src/itevi-mlde
cat .dockerignore
du -sh --exclude=.git .
```

**GATE 11** — `.dockerignore` excludes `.git`, `out/`, `ref/`, `notebooks/`, and
`*.parquet`, and the context is single-digit MB. If it is gigabytes, find the leak
before building anything.

---

## Phase 12 — Tue: clear the two blockers

The container refuses to run without these, deliberately.

### 12.1 The parent sequence

```bash
cd ~/src/itevi-mlde
$EDITOR config/parents/itevi.fasta
```

Replace the placeholder with the real I-TevI sequence — from UniProt, or from the
Loedige et al. construct, whichever the wet-lab lead actually expresses. Those can
differ by tags and linkers; record which you chose in `DESIGN.md`.

Verify:

```bash
PYTHONPATH=shared/src python - <<'CHECK'
from pathlib import Path
from itevi_core import variants, ids
seq = variants.read_fasta(Path("config/parents/itevi.fasta"))
print(f"length      {len(seq)}")
print(f"sha256[:12] {ids.sequence_sha256(seq)[:12]}")
for resi, aa in [(27, "R"), (40, "H"), (75, "E")]:
    obs = seq[resi - 1]
    print(f"{aa}{resi}: {'OK' if obs == aa else 'MISMATCH -> found ' + obs}")
CHECK
```

**GATE 12.1** — all three catalytic residues match. A mismatch means the numbering
convention differs (initiator Met counted or not), and **every Block 2 index
downstream is wrong**. Fix it here, not after scoring 1024 variants.

### 12.2 `config/library.yaml`

Fill in the 10 differing positions from the paper. Six are named in the handoff
(30, 33, 36, 37, 38, 39); four are not.

**Reconcile position 39 first.** The handoff says both "C39R" (C is parent, R is
the substitution) and lists "C39" among the selected residues (C is the
substitution). Those are opposite readings. Week 2 ranks exactly these variants
against library background, so inverted polarity makes the retrospective
validation silently measure the wrong thing — and it will look like a mediocre
result rather than a bug.

```bash
PYTHONPATH=shared/src python - <<'CHECK'
from pathlib import Path
from itevi_core import variants
cfg = variants.load_config(Path("config/library.yaml"))
seq = variants.read_fasta(Path("config/parents/itevi.fasta"))
variants.validate_parent(seq, cfg)
print("library.yaml validates against the parent")
CHECK
```

**GATE 12.2** — validates without raising. Until it does, `scan` mode still works
(it needs only the window), so Phases 13–16 are not blocked — but Week 2 is.

---

## Phase 13 — Wed: watch the cache fail

Build the deliberately-wrong image and time it three ways.

```bash
cd ~/src/itevi-mlde
tmux new -s build

time docker build -f containers/esm2-scorer/Dockerfile.naive -t esm2-naive .   # (1) cold
time docker build -f containers/esm2-scorer/Dockerfile.naive -t esm2-naive .   # (2) no-op
touch containers/esm2-scorer/src/esm2_scorer/cli.py
time docker build -f containers/esm2-scorer/Dockerfile.naive -t esm2-naive .   # (3) after an edit
```

**GATE 13** — (3) is nearly as slow as (1). That is the lightbulb: a one-character
edit reinstalled torch *and* re-downloaded 2.5 GB of weights, because `COPY . .`
sits above `pip install`.

See exactly where invalidation started:

```bash
docker build -f containers/esm2-scorer/Dockerfile.naive --progress=plain -t esm2-naive . 2>&1 \
  | grep -E 'CACHED|RUN|COPY'
```

---

## Phase 14 — Wed: fix it, one defect at a time

```bash
diff -u containers/esm2-scorer/Dockerfile.naive containers/esm2-scorer/Dockerfile | less
```

Three defects, in order of cost:

1. **`COPY . .` above `pip install`** → every edit reinstalls torch.
   Fix: `COPY requirements.txt` → install → `COPY src`.
2. **Weights fetched after dependencies** → any dep bump re-pulls 2.5 GB.
   Fix: `curl` the checkpoints in their own layer *above* the pip install, so the
   weights layer does not depend on the Python environment at all.
3. **Single stage on `-devel`** → ships `nvcc` and headers to production.
   Fix: Phase 17.

Apply 1 and 2 one at a time to `Dockerfile.naive`, re-timing after each. Six
numbers total in `learning-log.md`.

**GATE 14** — after both fixes, edit-and-rebuild is **under 30 seconds**.

---

## Phase 15 — Thu: GPU

### 15.1 Build the real image

```bash
cd ~/src/itevi-mlde
just esm2-build
just shas
docker images | grep esm2-scorer
```

Under the hood that is a repo-root build:
`docker build -f containers/esm2-scorer/Dockerfile --build-arg GIT_SHA=<path-scoped-sha> -t itevi-mlde/esm2-scorer:<sha> .`

### 15.2 Three checks, in order — each localises a different failure

```bash
SHA=$(git log -1 --format=%h -- containers/esm2-scorer shared/)
IMG=itevi-mlde/esm2-scorer:$SHA

# (a) does the toolkit inject the driver?
docker run --rm --gpus all "$IMG" --help
docker run --rm --gpus all --entrypoint nvidia-smi "$IMG" -L

# (b) does torch see CUDA? failing HERE means a CPU wheel, not a toolkit problem
docker run --rm --gpus all --entrypoint python "$IMG" -c \
  "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available(), torch.cuda.get_device_name(0))"

# (c) UID-agnostic + PATH -- exactly what Nextflow does in Week 10
docker run --rm --user 4242:4242 "$IMG" --help
docker run --rm --user 4242:4242 --entrypoint /bin/bash "$IMG" \
  -lc 'command -v esm2-score && python -c "import itevi_core, esm2_scorer; print(\"imports OK\")"'
```

**GATE 15** — all three pass; (b) prints `True` and `NVIDIA RTX A4000`. Check (c)
matters because Nextflow runs containers as an arbitrary UID and bypasses
`ENTRYPOINT` entirely — failing it in October costs a day to diagnose.

### 15.3 Smoke test — `scan` mode

`scan` needs only the parent and the window, so it runs before `library.yaml` is
finished, and it exercises the whole path end to end for a few seconds of compute.

```bash
mkdir -p "$ITEVI_SCRATCH/out/esm2/scan"
docker run --rm --gpus all --user "$(id -u):$(id -g)" --shm-size 2g \
  -e IMAGE_TAG="$IMG" -e GIT_SHA="$SHA" \
  -v "$PWD/config:/config:ro" -v "$ITEVI_SCRATCH:/scratch" \
  "$IMG" scan --output /scratch/out/esm2/scan/window.parquet --device cuda
```

**GATE 15.3 — the first scientific check of the campaign.** The printed summary
should show the catalytic positions with **low entropy and `parent_rank == 1`**:
ESM-2 ought to be confident that R27, H40 and E75 are what they are. If the active
site looks like noise, ESM-2 has not learned this fold and the Layer 1 premise is
in trouble — which is exactly what the Week 3 memo exists to establish, arriving
two weeks early and free.

---

## Phase 16 — Thu: batch-size calibration for 16 GB

The default was written for a 24 GB card. Measure. In a second pane (`Ctrl-b "`):

```bash
watch -n1 nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv
```

Walk it up:

```bash
for bs in 8 16 32 64; do
  echo "== batch_size=$bs"
  docker run --rm --gpus all --user "$(id -u):$(id -g)" --shm-size 2g \
    -v "$PWD/config:/config:ro" -v "$ITEVI_SCRATCH:/scratch" \
    "$IMG" scan --output "/scratch/out/esm2/scan/bs_$bs.parquet" \
    --device cuda --batch-size $bs || echo "  OOM at $bs"
done
```

**GATE 16.1** — largest batch size leaving ~2 GB headroom. Put it in the
`justfile`. Keep the headroom: on a shared GPU someone else's job can arrive
mid-run.

**GATE 16.2 — batch size must not change the answer.** This is the property the
Week 10 reproduction test rests on:

```bash
PYTHONPATH=shared/src python - <<'CHECK'
import glob, os
import pandas as pd
fs = sorted(glob.glob(os.path.expandvars("$ITEVI_SCRATCH/out/esm2/scan/bs_*.parquet")))
base = pd.read_parquet(fs[0]).set_index(["resi", "aa"])["logprob"]
for f in fs[1:]:
    d = pd.read_parquet(f).set_index(["resi", "aa"])["logprob"]
    print(os.path.basename(f), "max abs diff =", (d - base).abs().max())
CHECK
```

Differences must be ~1e-6 or below. **~1e-3 means TF32 is on somewhere**
(Phase 7.2) — check the `allow_tf32` column before hunting for a batching bug.

---

## Phase 17 — Fri: multi-stage, and the full library

### 17.1 Size comparison

```bash
docker images --format 'table {{.Repository}}:{{.Tag}}\t{{.Size}}' | grep esm2
docker history "$IMG" --human --format 'table {{.Size}}\t{{.CreatedBy}}' | head -20
```

**GATE 17.1** — the multi-stage image is materially smaller than `esm2-naive`;
expect roughly 6–8 GB against 12–16 GB. Record both.

### 17.2 Optional, 20 minutes

Build a variant on a plain `ubuntu:22.04` base and let pip supply the entire CUDA
stack (`environments.md` §2.4 explains why this works). Expect to save 1.5–2 GB.
Whether you keep it or not, understanding *why* it works is what stops GPU
containers feeling like voodoo.

### 17.3 The deliverable

Requires GATE 12.2.

```bash
tmux new -s run
cd ~/src/itevi-mlde

mkdir -p "$ITEVI_SCRATCH/libraries"
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD/config:/config:ro" -v "$ITEVI_SCRATCH:/scratch" \
  "$IMG" build-library --output /scratch/libraries/library_1024.parquet

just esm2-score library_1024 marginal      # seconds -- 18 forward passes total
time just esm2-score library_1024 both     # 20-40 min -- PLL is L passes per variant
```

**GATE 17.3** — 1024 rows, no NaNs, provenance populated.

---

## Phase 18 — Fri: cross-checks and sign-off

### 18.1 Inspect the output

```bash
PYTHONPATH=shared/src python - <<'CHECK'
import glob, os
import pandas as pd
f = sorted(glob.glob(os.path.expandvars("$ITEVI_SCRATCH/out/esm2/library_1024/*.parquet")))[-1]
d = pd.read_parquet(f)
print(d.shape)
print([c for c in d.columns if c.startswith("esm2_650m")])
print(d[["variant_id", "display_name", "n_mut", "esm2_650m_masked_marginal"]].head())
print(d[["allow_tf32", "gpu_name", "dtype", "torch_version", "parent_sha256", "image_digest"]].iloc[0])
print("NaNs:", d.filter(like="esm2_650m").isna().sum().sum())
CHECK
```

**GATE 18.1** — feature columns are `esm2_650m_*`; `variant_id` is `v_<12 hex>`;
`allow_tf32` is `False`; `image_digest` reads `unset` (correct — nothing has been
pushed to a registry yet, and a fabricated value would be worse); `parent_sha256`
populated.

### 18.2 Free cross-check: CPU image against GPU image

The marginal path is 18 forward passes, so the CPU image scores the same library
in under a minute on 112 threads. Two independent code paths, same numbers.

```bash
just esm2-build-cpu
CPU_IMG=itevi-mlde/esm2-scorer:cpu-$SHA
docker run --rm --user "$(id -u):$(id -g)" -e OMP_NUM_THREADS="${OMP_NUM_THREADS:-16}" \
  -v "$PWD/config:/config:ro" -v "$ITEVI_SCRATCH:/scratch" \
  "$CPU_IMG" score --input /scratch/libraries/library_1024.parquet \
  --output /scratch/out/esm2/library_1024/cpu_check.parquet --mode marginal --device cpu
```

```bash
PYTHONPATH=shared/src python - <<'CHECK'
import glob, os
import pandas as pd
base = os.path.expandvars("$ITEVI_SCRATCH/out/esm2/library_1024")
cpu = pd.read_parquet(f"{base}/cpu_check.parquet").set_index("variant_id")
gpu_f = sorted(g for g in glob.glob(f"{base}/*.parquet") if "cpu_check" not in g)[-1]
gpu = pd.read_parquet(gpu_f).set_index("variant_id")
col = "esm2_650m_masked_marginal"
print("max abs diff:", (cpu[col] - gpu[col]).abs().max())
print("spearman:", cpu[col].corr(gpu[col], method="spearman"))
CHECK
```

**GATE 18.2** — max absolute difference ~1e-4 or below, Spearman ≈ 1.0. A larger
gap points at the token-offset indexing (the BOS `+1`) or a dtype mismatch.
Finding that now is worth an hour; finding it in the Week 3 memo is not.

### 18.3 Week 1 acceptance

- [ ] `./scripts/doctor.sh` → 0 FAIL
- [ ] Both repos exist, `pre-commit` installed in each, layer-cache spike written up
- [ ] Parent sequence validated; catalytic residues at the expected indices
- [ ] `library.yaml` complete; **position-39 polarity reconciled against the paper**
- [ ] Naive vs multi-stage image sizes recorded; six build timings logged
- [ ] Image runs as `--user 4242:4242` with both packages importable
- [ ] Batch size calibrated for 16 GB and proven not to change results
- [ ] `scan` shows low entropy and `parent_rank == 1` at R27, H40, E75
- [ ] 1024 rows scored in `both` mode, no NaNs, provenance populated
- [ ] CPU/GPU marginal cross-check agrees to ~1e-4
- [ ] Data contract v0 sent to the wet-lab lead

Carry into Week 2: ECR repos and the first push (when `image_digest` starts being
real), `evmutation-scorer` against the GIY-YIG MSA, and the first retrospective
spike result.

---

## Appendix A — Weekly housekeeping

```bash
docker system df                              # where the disk went
docker builder prune --filter until=168h      # BuildKit cache is invisible to `docker images`
df -h "$DOCKER_DATA_ROOT" "$ITEVI_SCRATCH"
nvidia-smi                                    # who else is on the card
```

## Appendix B — Rollback

```bash
sudo systemctl stop docker docker.socket
sudo mv /etc/docker/daemon.json.bak /etc/docker/daemon.json   # if you kept one
sudo systemctl start docker

# full removal
sudo apt-get purge -y docker-ce docker-ce-cli containerd.io \
                      docker-buildx-plugin docker-compose-plugin nvidia-container-toolkit
sudo rm -rf "$DOCKER_DATA_ROOT" /etc/apt/sources.list.d/docker.list \
            /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo gpasswd -d "$USER" docker
```

Never purge the NVIDIA *driver* as part of a Docker rollback — it is unrelated,
already correct at 570.133.07, and reinstalling it costs a reboot.

## Appendix C — What this host does *not* need

Recorded so you do not do it out of habit:

- **`ubuntu-drivers install`** — driver 570.133.07 is current and correct. Running
  this can only downgrade or break DKMS state.
- **A CUDA toolkit on the host** (`nvcc`, `cuda-toolkit-12-4`) — the container
  supplies everything, and PyTorch ships its own runtime libraries (§2.4). Host
  CUDA installs are the main way driver/toolkit version confusion starts.
- **conda/mamba** — `uv` plus a venv is leaner, faster, and does not fight pip
  inside a container.
- **`--memory` limits** — with 1 TB of RAM, memory pressure is not your failure
  mode. Set `--shm-size 2g` anyway (§7.3); that default is 64 MB regardless of
  host RAM.
