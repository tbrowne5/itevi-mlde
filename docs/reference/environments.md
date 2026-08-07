# Environments — full reference

Two machines, one pipeline. This document explains what each piece does, why it
is there, and what breaks when it is missing. Read §1–§2 once for the mental
model; §4–§5 are the actual build-out; §10 is what you come back to when
something fails at 4pm.

---

## 1. Mental model

### 1.1 What a container actually is

A container is not a virtual machine. There is no guest kernel, no virtual
hardware, no boot. A container is **a normal Linux process** that the kernel has
been asked to lie to, in five specific ways:

| Mechanism | What it hides |
|---|---|
| mount namespace | the host filesystem — the process sees only the image's files |
| PID namespace | other processes — your process is PID 1 inside |
| network namespace | host interfaces — the container gets its own stack |
| user namespace | UID mapping (optional; off by default in rootful Docker) |
| cgroups v2 | limits CPU, memory, device access |

Everything else is shared with the host, and the most important shared thing is
**the kernel**. This single fact explains most of the constraints in this
document:

- A container cannot run a different OS. "Ubuntu 22.04 container on a Debian
  host" works because both are Linux and the image only supplies userspace.
- A container cannot run a different CPU architecture without emulation, because
  the host kernel must actually execute the binaries.
- A container cannot supply its own GPU driver, because drivers are kernel
  modules and the kernel belongs to the host. That is §2.

The image itself is a stack of read-only filesystem layers, unioned by `overlay2`
and topped with a thin writable layer discarded on `--rm`. Each `RUN`, `COPY`, or
`ADD` produces exactly one layer; `docker history` shows them. Layers are
content-addressed and shared between images, which is why a second 6 GB image
costs only a few hundred MB on disk if it shares a base.

**Why you care this week:** layer boundaries are the unit of caching. Where the
2.5 GB model download sits in the stack is the difference between a 6-minute
rebuild loop and an 8-second one.

### 1.2 macOS is a special case: there is a hidden VM

macOS has no Linux kernel, so Docker Desktop / OrbStack / colima all run a
**lightweight Linux VM** (via Apple's Virtualization.framework) with the Docker
daemon inside it. The `docker` command on your Mac is a client talking over a
socket to a daemon in that VM.

Non-obvious consequences:

- The VM on an M5 is **arm64 Linux**. `docker run ubuntu uname -m` prints
  `aarch64`, not `x86_64`.
- Bind mounts cross a filesystem-sharing boundary (VirtioFS), much slower than
  native for many small files. Irrelevant for us — we mount a couple of Parquet
  files — but it is the origin of "Docker is slow on Mac".
- The VM has a fixed disk allocation. Two multi-GB images can fill it while your
  Mac reports 400 GB free. Raise it in the runtime's settings, or prune.

### 1.3 Architecture: arm64 vs amd64

An M5 executes ARM64 instructions. `nvidia/cuda:*` images contain x86-64 ELF
binaries. The host kernel cannot execute those, so Docker refuses the image
unless emulation is available.

`--platform linux/amd64` enables emulation by one of two routes:

- **QEMU user-mode**, registered in the VM via `binfmt_misc`. Correct, roughly
  10–20× slower for compute-heavy work.
- **Rosetta 2**, which Docker Desktop and OrbStack can use instead. Often within
  2–3× of native for ordinary userspace code.

So the speed argument against emulation is weaker than usually stated. **The real
blocker is not speed, it is CUDA.** There is no path from an Apple Silicon Mac to
an NVIDIA GPU: no driver, no device, no emulation that helps. An emulated amd64
container on the Mac is a CPU-only container with extra steps.

That is why the split in §3 exists, and why we keep a genuinely useful
arm64-native CPU image (`Dockerfile.cpu`) rather than pretending to run the GPU
one locally.

---

## 2. The GPU stack

### 2.1 Who owns what

Five layers, and which side of the container boundary each lives on:

| Layer | Example | Lives on |
|---|---|---|
| Kernel module | `nvidia.ko` | **host only** |
| Driver userspace | `libcuda.so`, `libnvidia-ml.so`, `nvidia-smi` | **host**, injected into container |
| CUDA runtime | `libcudart.so.12` | **container** |
| Math/DNN libs | `libcublas`, `libcudnn` | **container** |
| Framework | PyTorch | **container** |

The kernel module and the driver userspace libraries are **version-locked to each
other**. `libcuda.so.550.54` will not talk to `nvidia.ko` 535. This is why an
image must never ship `libcuda.so` — it would be a build-time version running
against whatever kernel module the host happens to have.

### 2.2 What nvidia-container-toolkit actually does

It registers a **runtime hook** with the Docker daemon. When a container starts
with `--gpus`, the hook runs before your process and:

1. Bind-mounts the host's driver userspace libraries into the container
   (`libcuda.so`, `libnvidia-ml.so`, and friends), then runs `ldconfig`.
2. Bind-mounts host binaries such as `nvidia-smi`.
3. Creates the device nodes (`/dev/nvidia0`, `/dev/nvidiactl`, `/dev/nvidia-uvm`)
   inside the container and grants cgroup device access.

So when you run `nvidia-smi` inside a container, **you are running the host's
binary against the host's driver**. It was never in the image. If `nvidia-smi`
works inside a container but PyTorch reports `cuda.is_available() == False`, the
hook worked and the problem is above it — almost always a CPU torch wheel.

Docker alone has no concept of a GPU. Installing the toolkit and running
`nvidia-ctk runtime configure --runtime=docker` (then restarting dockerd) is what
makes `--gpus` mean anything. This is the most commonly missed step.

### 2.3 Version compatibility, precisely

Two independent questions, usually conflated:

**Does the driver support this CUDA major version?**
CUDA 12.x requires driver ≥ 525.60.13. Hard floor.

**Does the driver support this CUDA *minor* version?**
Normally each minor bump wants a newer driver (12.4 → 550.54.14). But CUDA 12
ships **minor version compatibility**: a CUDA 12.4 application runs on any driver
supporting 12.0, provided it does not call APIs introduced after 12.0. PyTorch's
builds respect this.

Net rule: **driver ≥ 525 is sufficient**, ≥ 550 is comfortable. `doctor.sh`
encodes exactly this and tells you which bucket you are in — worth knowing before
you go asking a sysadmin for an upgrade you do not need.

### 2.4 Why the base image barely matters

PyTorch's wheels from `download.pytorch.org/whl/cu124` do not link against a
system CUDA. They pull `nvidia-cuda-runtime-cu12`, `nvidia-cublas-cu12`,
`nvidia-cudnn-cu12` and friends as ordinary Python dependencies and load them
from `site-packages`. Roughly 2 GB of it.

Which means `nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04` supplies a second copy
of libraries PyTorch will not use. A plain `ubuntu:22.04` or `python:3.11-slim`
base plus the pip wheels works, and is 1.5–2 GB smaller.

We keep the CUDA base anyway, for two defensible reasons: it is what downstream
tools expect (LigandMPNN, ProteinMPNN forks, anything with a custom CUDA kernel
needing `nvcc` at build time), and it makes the CUDA version an explicit,
auditable fact of the image rather than an implicit consequence of a pip index
URL. Friday's size experiment is still worth running — measuring the difference
is how this paragraph becomes knowledge.

---

## 3. The machines

| | MacBook Pro M5 | Ubuntu server |
|---|---|---|
| arch | arm64 | x86_64 |
| accelerator | Apple MPS, unified memory | NVIDIA CUDA |
| role | learn, edit, explore, build the CPU image | build and run everything real |
| container GPU | impossible | yes |
| produces anchor numbers | **never** | yes |
| always available | yes | VPN-dependent |

**The anchor rule.** fp32 on MPS and fp32 on CUDA agree to roughly 1e-4 per
position — different reduction orders and kernels, not a bug. Summed over 245
positions in a PLL, that is visible in the third decimal. Exploration on the Mac
is fine and encouraged; the number that goes in the Week 3 PI memo, and that the
Week 10 pipeline test must reproduce, is whatever the container produced on the
server. Label every plot with its provenance — which is why the scorer stamps
`dtype`, `cuda_version`, and `torch_version` into every output row.

---

## 4. Server build-out

Run `./scripts/bootstrap-ubuntu.sh` with no flags first. It prints every command
it would run and changes nothing. Read the plan, then `--apply`.

### 4.1 Preflight

Confirm `dpkg --print-architecture` is `amd64`, and check `lspci | grep -i nvidia`
for hardware. If a driver is installed but `nvidia-smi` reports a "driver/library
version mismatch", the kernel module was updated without a reboot — reboot before
anything else.

### 4.2 NVIDIA driver

The script installs only if absent, via `ubuntu-drivers install`, which picks the
recommended branch for your hardware. Three notes:

- **`-server` driver variants** (`nvidia-driver-550-server`) are the right choice
  on headless machines: no X/display components, fewer packages, fewer things to
  break.
- **Driver installs require a reboot.** Until then the new userspace libraries
  and the running kernel module disagree.
- **DKMS and kernel upgrades.** The module is rebuilt per kernel. An unattended
  kernel upgrade can leave it unbuilt, so `nvidia-smi` breaks after a reboot you
  did not ask for. On a machine you care about: `sudo apt-mark hold
  linux-image-generic`.

### 4.3 Docker Engine

We install from Docker's official apt repository rather than `curl | sh`. The
convenience script does the same thing but pins nothing, warns that it is not for
production, and leaves no apt source for later upgrades. The repo route gives you
`apt upgrade` and a signed keyring.

Packages: `docker-ce` (daemon), `docker-ce-cli` (client), `containerd.io` (the
actual container runtime), `docker-buildx-plugin` (BuildKit), and
`docker-compose-plugin`.

Do **not** install `docker.io` from Ubuntu's own repos — it is older and its
BuildKit support lags in ways that will bite during Wednesday's cache work.

### 4.4 The docker group

Adding yourself to `docker` lets you run `docker` without `sudo`. Understand the
grant: **membership in the `docker` group is equivalent to root.** Anyone in it
can run

```bash
docker run -v /:/host -it ubuntu chroot /host
```

and be root on the host. There is no privilege boundary. Fine on your own box, a
real decision on a shared one. The alternatives (rootless Docker, Podman) trade
this away for a more complex GPU setup — not worth it here, but worth knowing the
tradeoff exists.

Group membership is evaluated at login. `newgrp docker` gives you a shell with it
applied; otherwise log out and back in.

### 4.5 Storage and the data root

Everything Docker owns — image layers, containers, volumes, build cache — lives
under `DockerRootDir`, by default `/var/lib/docker`. Budget:

| Item | Size |
|---|---|
| CUDA base images | ~3 GB |
| torch + nvidia-* wheels | ~4 GB |
| ESM-2 650M weights | 2.5 GB |
| final GPU image | 6–8 GB |
| naive `-devel` image (kept for comparison) | 12–16 GB |
| BuildKit cache after a week of iteration | 10–30 GB |

Call it 60 GB to be comfortable. On most lab servers `/var` is on a small root
partition, so `--data-root /scratch/docker` points it at the large volume.

Two hard constraints on where it can go:

- **The filesystem must support `overlay2`**: ext4, or xfs with `ftype=1` (check
  with `xfs_info /scratch | grep ftype`).
- **It cannot be NFS.** overlay2 does not work there. If the big volume is
  network-mounted, Docker still needs local disk even though your *data* can live
  on the NFS mount.

Also: **BuildKit cache is invisible to `docker images`**. When disk vanishes
mysteriously, `docker system df` is the diagnosis and `docker builder prune
--filter until=168h` is the cure.

### 4.6 NVIDIA Container Toolkit

Per §2.2. Installed from NVIDIA's apt repo, then:

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

`nvidia-ctk` edits `/etc/docker/daemon.json` to register the `nvidia` runtime.
The restart is required — the daemon reads that file only at startup. Verify:

```bash
docker info --format '{{json .Runtimes}}'    # should mention nvidia
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

If the second fails while the first succeeds, the usual cause is a base image tag
that has moved (§10).

### 4.7 Tooling, and why each is there

| Tool | Why |
|---|---|
| `uv` | Resolves and installs Python deps 10–100× faster than pip, and `uv pip compile` produces a fully-pinned lockfile. Speed matters because you rebuild the venv layer often. |
| `just` | A command runner, not a build system. Every non-obvious invocation lives in the `justfile`, making it discoverable (`just --list`) and portable — the anti-shell-history device. |
| `jq` | Docker, AWS, and Nextflow all emit JSON. |
| `tmux` | §6.4. Non-negotiable over SSH. |
| `direnv` | Loads `.envrc` on `cd`, unloads on exit. Keeps env vars scoped to the project instead of accumulating in `.bashrc`. Requires `direnv allow` per checkout — that prompt is the security model, since `.envrc` is arbitrary shell from your repo. |
| `rsync` | Moving data between hosts with resume and checksums. |
| AWS CLI **v2** | Week 2 (ECR) onward. Installed from Amazon's zip, not apt — Ubuntu's package is v1 and differs in ECR auth and SSO. |
| `git`, `build-essential`, `python3-venv` | Obvious. |

### 4.8 Filesystem layout

Decide once, put it in the `justfile`, not in your head:

```
/scratch/docker/            # docker data root — local disk, not NFS
/scratch/<you>/itevi/       # working data: parquet, intermediates, big files
  ├── out/                  # container outputs, bind-mounted to /work
  └── ref/                  # GIY-YIG MSA, structures, large static inputs
~/src/esm2-scorer/          # the git checkout — code only, small
```

Rule: **code in the repo, data on scratch, nothing that matters in `$HOME`.**
Home directories do not move to a new server; the repo and the registry do.

If `/scratch` is local NVMe and `/home` is NFS, this also buys you materially
faster weight loading and Parquet writes.

---

## 5. Mac build-out

### 5.1 Container runtime

Docker Desktop, OrbStack, or colima — same `docker` CLI, different VM
implementations.

- **OrbStack** — fastest start, lowest idle RAM, Rosetta built in, free for
  personal use.
- **Docker Desktop** — the reference implementation; note the licence requires a
  paid subscription for companies over 250 employees or $10M revenue.
- **colima** — fully open source, CLI-driven, no GUI.

Any is fine. No project deliverable depends on Mac-side Docker.

### 5.2 The native MPS environment

`setup-mac.sh` creates `.venv-local` with `uv` and installs
`requirements-local.txt` — same pins as the container, plain PyPI wheels instead
of the cu124 index. Deliberately a **separate file**: if it were the same file,
someone would eventually run `pip install -r requirements.txt` on the Mac, get a
CPU-only torch silently, and lose an afternoon wondering why the server is only
4× faster.

Two things make MPS work:

- `PYTORCH_ENABLE_MPS_FALLBACK=1`, set in `.envrc`. PyTorch's MPS backend does
  not implement every operator; without this a missing kernel raises, with it
  that op silently runs on CPU. Silent fallback is acceptable here because this
  environment is exploratory by definition — and it is exactly what we *forbid*
  in the container (§7.4). Same word, opposite decision, because the contracts
  differ.
- Unified memory: the model shares system RAM, so 650M at fp32 (2.6 GB weights,
  ~6 GB working) is comfortable.

### 5.3 What the Mac can and cannot do

**Can:** the whole Docker learning arc against `Dockerfile.cpu` at native speed;
masked-marginal scoring of the full 1024-member library in under a minute (11
forward passes total); all code, config, and doc work; plotting and analysis.

**Cannot:** run the CUDA image at all; full PLL at a useful rate (250k forward
passes on MPS is hours); produce anchor numbers.

---

## 6. Connecting the two

### 6.1 SSH config

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

Line by line:

- **`ControlMaster auto` + `ControlPath` + `ControlPersist`** — connection
  multiplexing. The first connection opens a real TCP session and leaves a unix
  socket behind; every subsequent `ssh`, `scp`, `rsync`, or VS Code channel
  reuses it, skipping TCP setup and key exchange. A 1.5 s connect becomes ~50 ms,
  hundreds of times a day. `ControlPersist 10m` keeps the master alive that long
  after the last session closes.
  - Gotcha: if the master hangs, everything hangs. `ssh -O exit itevi-gpu` kills
    it, or delete the socket.
- **`ServerAliveInterval 30` / `CountMax 6`** — the client pings every 30 s and
  gives up after 6 unanswered. Without it, NAT and VPN idle timeouts silently
  drop long sessions and you find out when you try to type.
- **`Compression no`** — on a fast link, compression costs more CPU than it
  saves. Enable only over genuinely slow links.
- **Ed25519 keys** over RSA: shorter, faster, no key-size arguments.

### 6.2 Agent forwarding — read before enabling

`ForwardAgent yes` lets you `git push` from the server using the private key that
never leaves your Mac. Convenient, and no private key lands on the server.

The cost: while your session is live, **anyone with root on that server can use
your agent socket** to authenticate as you to anything the key opens — other
servers, GitHub. Where you are the only root, a non-issue. On a shared box, a
real exposure.

Safer options, roughly in order:

1. **`ProxyJump` / `ssh -J`** for onward SSH — no agent exposure at all.
2. **A separate ed25519 key generated on the server**, registered as a GitHub
   deploy key scoped to this one repo. Compromise is contained.
3. **`ForwardAgent yes` only inside a specific `Match` block**, never globally,
   with a confirmation-gated key: `ssh-add -c` prompts on the Mac for every use.

The scripts do not set `ForwardAgent` for you. Choose deliberately.

### 6.3 VS Code Remote-SSH

Worth knowing what it does, because it explains the failure modes. On first
connect it downloads a **VS Code Server** into `~/.vscode-server` on the remote
host and runs it there. Your Mac runs only the UI. Extensions split in two: UI
extensions (themes) stay local; workspace extensions (Python, Ruff, Docker)
install onto the remote and execute there — which is why the interpreter picker
sees the server's venvs and the Docker panel sees the server's images.

Implications:

- **`~/.vscode-server` is a few hundred MB per host.** On a quota'd home
  directory that matters.
- **Extensions install per host.** Use Settings Sync or a
  `.vscode/extensions.json` in the repo so a new server converges automatically.
- **The integrated terminal is a server-side shell.** Everything you type runs
  there, which is what you want — and is why `docker context` (§6.5) is mostly
  unnecessary in this setup.
- **It is not a substitute for tmux.** Closing the laptop kills the terminal's
  process group.

### 6.4 tmux

The rule: **anything longer than a coffee starts inside tmux.**

```bash
tmux new -s itevi          # create
# Ctrl-b d                 # detach — the process keeps running
tmux a -t itevi            # reattach, from any machine, after any disconnect
tmux ls                    # what is running
```

A full PLL run is 20–40 minutes; a no-cache build is comparable. A VPN blip
without tmux loses both. With tmux, the process is owned by the tmux server
rather than your SSH session, and survives.

Alternatives (`nohup`, `systemd-run --user --scope`) work but give you no way to
watch the running process interactively, which you will want.

### 6.5 Docker contexts

To drive the server's daemon from a Mac-side terminal:

```bash
docker context create itevi-gpu --docker "host=ssh://itevi-gpu"
docker context use itevi-gpu
docker context ls
```

The CLI now talks to the remote daemon over SSH. Builds execute on the server;
`docker images` lists the server's images. The build context (your local
directory, minus `.dockerignore`) is tarred and streamed over SSH on **every**
build — which is why `.dockerignore` matters more here than usual.

With Remote-SSH you mostly do not need this, and it introduces a hazard: `just
build` against the wrong context is a confusing five minutes. `just context`
prints the current one; run it whenever something surprises you.

### 6.6 Moving data

```bash
rsync -avzP --exclude '.git' ~/src/esm2-scorer/ itevi-gpu:~/src/esm2-scorer/
rsync -avP itevi-gpu:/scratch/you/itevi/out/*.parquet ./out/
```

`-P` gives progress and resume. Prefer `git` for code and `rsync` only for data;
if you find yourself rsyncing source, the workflow has drifted from §6.3.

---

## 7. Running containers correctly

### 7.1 Bind mounts and the UID problem

```bash
docker run --rm -v $PWD/data:/data:ro -v $PWD/out:/work esm2-scorer:abc123 ...
```

Bind mounts pass **numeric UIDs**, not usernames. The container's `scorer` user
is UID 1000. If your host UID is also 1000 (typical for the first account on a
fresh Ubuntu install) outputs land owned by you and life is pleasant. If your
host UID is 1001 — common wherever you are not the first account — the container
writes files owned by whoever *is* UID 1000, and you cannot delete your own
outputs.

Two fixes:

```bash
docker run --user "$(id -u):$(id -g)" ...   # run as yourself
```

or make the image UID-agnostic, which is the better answer and matters for a
second reason.

**Forward-looking: Nextflow (Weeks 8–10) runs containers as `-u $(id -u):$(id -g)`
by default, and bypasses `ENTRYPOINT` entirely** — it executes `/bin/bash -ue
.command.sh` inside. So the image must:

- work as an arbitrary, unknown UID with no matching `/etc/passwd` entry;
- never write outside `/tmp` and the mounted work directory;
- have `esm2-score` on `PATH` for a non-login shell (it is, via `/opt/venv/bin`);
- contain a shell — which is why `-slim` is fine but `distroless` is not.

Designing for that now costs nothing and saves a confusing week in October. Test
it on Friday: `docker run --user 4242:4242 ...` should behave identically.

### 7.2 GPU selection

```bash
docker run --gpus all ...                    # every GPU
docker run --gpus '"device=1"' ...           # only physical GPU 1
CUDA_VISIBLE_DEVICES=1 docker run --gpus all # same effect, applied inside
```

The doubled quoting on `device=` is a Docker CLI parsing quirk, not a typo. And
note that with `--gpus '"device=1"'` the GPU appears **as device 0** inside the
container — do not then also set `CUDA_VISIBLE_DEVICES=1`, or you select a device
that does not exist. Pick one mechanism and stay with it.

On a shared box, check `nvidia-smi` for other users' processes before queueing a
40-minute job.

### 7.3 Resource limits

```bash
--memory 32g --cpus 8 --shm-size 2g
```

`--shm-size` is the one that bites: the default `/dev/shm` is 64 MB, and PyTorch
DataLoader workers use shared memory to pass tensors. Not an issue for our
single-process scorer, but it is the classic "works locally, dies in the
pipeline" bug. Set it once and forget it.

### 7.4 Exit codes and the no-silent-fallback rule

The scorer exits 2 on bad input, 3 on missing CUDA, 4 on OOM. Non-zero is what
Nextflow's `errorStrategy` and Batch's retry logic key on, so getting these right
in Week 1 is what makes Week 9's retry behaviour meaningful — a tool that returns
0 on failure turns `retry` into an expensive no-op.

The deliberate choice: **`--device cuda` with no CUDA exits 3 rather than falling
back to CPU.** A job that quietly becomes 14 hours is worse than one that fails
in two seconds. Contrast §5.2, where fallback is explicitly enabled — different
environment, different contract, and the difference is written down.

---

## 8. Reproducibility: tags, IDs, digests

Three identifiers, routinely confused:

| | What it is | Stable? |
|---|---|---|
| **Tag** (`esm2-scorer:v1`) | a mutable human label | no — retaggable at will |
| **Image ID** (`sha256:abc…`) | hash of the image *config*, computed locally | yes, but local-only |
| **Digest** (`repo@sha256:def…`) | hash of the *manifest*, assigned by the registry | yes, globally |

`:latest` is not a version. It is a default string with no semantics.

The Week 1 catch: **a locally built image has no digest.** Digests are assigned on
push. So until ECR exists in Week 2, "the same image" can only be claimed via
image ID plus `docker save` — not proven. This is precisely why ECR is a Week 2
item and digest pinning a Week 3 concept, and why the scorer writes
`IMAGE_DIGEST` into every output row from day one even though it currently reads
`unset`. The column exists so nothing downstream changes when the value becomes
real.

Meanwhile the Week 1 transfer mechanism:

```bash
docker save esm2-scorer:abc123 | zstd -T0 | ssh other-host 'zstd -d | docker load'
```

`zstd` roughly halves wire time versus raw tar and is far faster than gzip.

---

## 9. Verifying a new host

The portability test: **can a fresh Ubuntu box reach a scored Parquet using only
commands that live in the repo?**

```bash
git clone <repo> && cd esm2-scorer
./scripts/bootstrap-ubuntu.sh                              # read the plan
./scripts/bootstrap-ubuntu.sh --apply --data-root /scratch/docker   # see docs/RUNBOOK-server-setup.md for the host-specific version
newgrp docker
./scripts/doctor.sh                                        # must reach 0 FAIL
just build
just library
just full-run
```

`doctor.sh` is the gate. It checks, in order: architecture; Docker CLI and daemon
reachability; buildx; driver presence and the version floor; GPU inventory and
current occupancy; **`--gpus all` end to end**; disk headroom at the data root;
tooling; and whether host PyTorch sees CUDA or MPS. It exits non-zero on any
FAIL, so it drops into CI later unmodified.

Then confirm the *science* matches, not merely that it ran: marginal scores from
a new host should agree with the anchor to ~1e-6 — same image, same dtype, same
weights, with only GPU model and driver differing. If they don't, something in
the chain is not what you think it is, and you want to know that now rather than
in Round 2.

---

## 10. Failure catalogue

| Symptom | Cause | Fix |
|---|---|---|
| `docker: permission denied ... /var/run/docker.sock` | not in `docker` group, or group not applied to this shell | `sudo usermod -aG docker $USER`, then `newgrp docker` |
| `could not select device driver "" with capabilities: [[gpu]]` | container toolkit missing or runtime not registered | install toolkit, `nvidia-ctk runtime configure --runtime=docker`, restart dockerd |
| `nvidia-smi` works on host, fails in container | toolkit installed, daemon not restarted | `sudo systemctl restart docker` |
| `nvidia-smi` works in container, `torch.cuda.is_available()` False | CPU torch wheel installed | check `--extra-index-url`; `torch.version.cuda` must not be `None` |
| `Failed to initialize NVML: Driver/library version mismatch` | kernel module updated, not rebooted | reboot |
| `manifest unknown` on the CUDA base | NVIDIA retagged (`cudnn8-runtime` → `cudnn-runtime`) | check Docker Hub for the current tag |
| `exec format error` | amd64 image on arm64 host | build/run on the server, or use `Dockerfile.cpu` |
| Build dies `no space left on device` while `df -h /` looks fine | data root is on a different, full filesystem | `docker system df`, prune, or relocate the data root |
| Disk full but `docker images` totals look small | BuildKit cache, invisible to `docker images` | `docker builder prune --filter until=168h` |
| Output files owned by someone else, undeletable | container UID 1000 ≠ your UID | `--user "$(id -u):$(id -g)"` |
| Every build re-downloads 2.5 GB of weights | weights layer sits below a layer that changed | move the weights `RUN` above the dependency install |
| Every code edit reinstalls torch | `COPY . .` above `pip install` | split: `COPY requirements.txt` → install → `COPY src` |
| SSH session dies mid-run | no keepalive, no tmux | `ServerAliveInterval`, and start jobs in tmux |
| VS Code hangs reconnecting | stale SSH control socket | `ssh -O exit itevi-gpu` |
| MPS run raises `NotImplementedError` for an op | missing MPS kernel | `PYTORCH_ENABLE_MPS_FALLBACK=1` (already in `.envrc`) |
| Scores differ slightly between two hosts | different dtype, torch version, or MPS vs CUDA | check the provenance columns before assuming a bug |

---

## 11. Security notes

- **`docker` group ≈ root.** §4.4. A deliberate grant, not a formality.
- **Secrets never enter image layers.** A file deleted in a later layer is still
  present in the earlier one and recoverable from the image. Use build secrets
  (`RUN --mount=type=secret`) or runtime env vars. `.dockerignore` keeps `.git`
  and credentials out of the build context in the first place.
- **`pre-commit` + `gitleaks`** on every commit, per the Week 0 plan. Set this up
  before the first push, not after — rewriting history to remove a key is much
  worse than preventing it.
- **Agent forwarding** — §6.2. Understand the exposure before enabling it.
- **IP hygiene.** The project brief requires all IP be clean for commercial use.
  Two consequences here: check the licence of every tool before it enters a
  container (ESM-2 is MIT; ProteinMPNN and LigandMPNN each need reading; several
  structure tools carry non-commercial terms), and record what you used in
  `DESIGN.md` as you go. Reconstructing licence provenance a year later is
  miserable.
- **Public write-ups** (the Week 21 portfolio pass) go past the PI before posting.

---

## 12. What it costs

| | Time | Disk |
|---|---|---|
| Server bootstrap | 15–30 min (plus reboot if a driver is installed) | ~2 GB |
| First GPU image build, cold | 15–25 min (2.5 GB weights + ~4 GB wheels) | 6–8 GB |
| Rebuild after a code edit, cache correct | 10–30 s | negligible |
| Rebuild after a code edit, cache wrong | 6–10 min | — |
| Mac setup | 10–15 min | ~5 GB |
| CPU image build on the Mac, cold | 5–8 min | ~3.5 GB |
| Full 1024 library, marginal mode | seconds (11 forward passes) | ~1 MB |
| Full 1024 library, PLL mode, A10G-class | 20–40 min | ~2 MB |

The gap between rows 3 and 4 is the entire point of Wednesday.
