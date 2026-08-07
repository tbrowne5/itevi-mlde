# WEEK 1 — the only file you need this week

Everything else in `docs/` is reference. Open it when you have a question, not
before. Follow this file top to bottom.

Every step below states **where** (which machine, which directory), **why** it
exists, **what to run**, and **how to know it worked**. If you are ever unsure
where you are, jump to "Lost?" at the bottom.

---

## 0. Orientation — read this once

### Two repos, and what belongs in each

| Repo | Lives at | Holds | You edit it |
|---|---|---|---|
| `itevi-mlde` | `~/src/itevi-mlde` (server) | code, config, Dockerfiles, docs | on the **server** |
| `itevi-notes` | `~/src/itevi-notes` (both) | your daily notes, Obsidian vault | on the **Mac** |

Simple rule: **if someone else needs it to do their job, it goes in `itevi-mlde`.
If it's your own memory of what happened, it goes in `itevi-notes`.**

### Data never goes in a repo

```
~/src/itevi-mlde/          ← code. Small. Git-tracked.
/scratch/$USER/itevi/      ← DATA. Big. Never git-tracked.
    libraries/             ← variant tables (input)
    out/esm2/              ← scores (output)
/scratch/docker/           ← Docker's own storage. You never touch this.
```

Why: a Parquet of 1024 sequences is small, but by Round 2 you'll have millions,
and a repo with binary data in its history is permanently slow to clone. The
separation costs nothing now and is unfixable later.

### The only five files you will edit this week

Everything else in the repo is read-only for you right now.

| # | File | When | Why it matters |
|---|---|---|---|
| 1 | `config/parents/itevi.fasta` | Tue | The real I-TevI sequence. Nothing runs without it. |
| 2 | `config/library.yaml` | Tue | The 10 Block 2 positions. **Blocks Week 2.** |
| 3 | `justfile` | Thu | One line: the batch size you measure. |
| 4 | `docs/data-contract-v0.md` | any day | Draft to send the wet-lab lead. **Blocks Week 3.** |
| 5 | `~/src/itevi-notes/daily/*.md` | daily | 5 minutes at day's end. |

If you find yourself editing something not on this list, stop and ask whether it
belongs this week.

### The five things that make the week a success

1. Container builds and runs on the GPU
2. 1024 variants scored, Parquet exists
3. Files 1 and 2 above resolved
4. Data contract sent
5. Four daily notes

Items 3 and 4 gate other people and other weeks. Protect those. Items 1, 2 and 5
gate only you and slip cheaply.

---

# TUESDAY

Two jobs: get the server working, and resolve the two blockers. The Docker
learning starts Wednesday.

## Step 1 — Look before you touch

**Where:** server, any directory
**Why:** you're about to change a shared machine. Five minutes of looking
prevents a bad afternoon.

```bash
# who and what am I on
hostnamectl
. /etc/os-release && echo "codename=$VERSION_CODENAME"   # expect: jammy

# the GPU
nvidia-smi
nvidia-smi --query-compute-apps=pid,used_memory --format=csv   # anyone else on it?

# where is there room?
df -hT
findmnt -t nfs,nfs4      # anything here CANNOT hold Docker's storage
```

**Check:** codename is `jammy`, `nvidia-smi` shows driver 570.133.07, and you've
found a local (non-NFS) filesystem with 100 GB+ free.

**Why non-NFS:** Docker's storage driver (`overlay2`) doesn't work on network
filesystems. Your *data* can live on NFS; Docker's internals cannot.

Pick your paths now — every later step uses them:

```bash
echo 'export DOCKER_DATA_ROOT=/scratch/docker' >> ~/.bashrc      # edit to your real path
echo 'export ITEVI_SCRATCH=/scratch/$USER/itevi' >> ~/.bashrc
source ~/.bashrc
echo "$DOCKER_DATA_ROOT | $ITEVI_SCRATCH"
```

## Step 2 — Install Docker

**Where:** server, any directory
**Why:** we install from Docker's own apt repo rather than Ubuntu's `docker.io`
package, because Ubuntu's is older and its build system misbehaves in exactly the
way Wednesday's lesson depends on.

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl gnupg jq

sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin

sudo usermod -aG docker "$USER"
newgrp docker
```

**Check:**

```bash
docker run --rm hello-world      # must work WITHOUT sudo
```

**Note:** being in the `docker` group is equivalent to root on this machine —
anyone in it can mount the host filesystem into a container. Fine on a box you
control; worth knowing before you add colleagues.

## Step 3 — Point Docker's storage somewhere with room

**Where:** server, any directory
**Why:** Docker's default `/var/lib/docker` is usually on a small root partition.
Your images plus build cache will be 40–60 GB. Filling the root partition on a
shared server is a bad first week.

```bash
docker info --format '{{.DockerRootDir}}'
df -h "$(docker info --format '{{.DockerRootDir}}')"
```

**If that shows under ~100 GB free**, move it:

```bash
sudo mkdir -p "$DOCKER_DATA_ROOT"
sudo systemctl stop docker docker.socket
echo "{\"data-root\": \"$DOCKER_DATA_ROOT\"}" | sudo tee /etc/docker/daemon.json
sudo rsync -aHAX /var/lib/docker/ "$DOCKER_DATA_ROOT"/
sudo systemctl start docker
```

**Check:**

```bash
docker info --format '{{.DockerRootDir}}'    # your new path
docker run --rm hello-world                  # still works
```

Only after both pass: `sudo rm -rf /var/lib/docker`.

## Step 4 — Make Docker able to see the GPU

**Where:** server, any directory
**Why:** **Docker on its own has no concept of a GPU.** The NVIDIA driver lives in
the host kernel and cannot be shipped inside an image. The container toolkit is a
hook that, when you pass `--gpus`, bind-mounts the host's driver libraries into
the container and creates the device nodes. Without it, `--gpus all` is an error.

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list > /dev/null

sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

The restart is not optional — the daemon reads its config only at startup.

**Check — this is the most important check of the week:**

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

You should see your A4000 and driver 570.133.07 **from inside a container**. Do
not continue until you do; every later problem will otherwise look like a
Dockerfile bug.

*You are not installing a driver.* Yours is current. `ubuntu-drivers install`
can only break things here.

## Step 5 — Tools and one setting

**Where:** server, any directory

```bash
sudo apt-get install -y tmux rsync unzip direnv python3-venv hmmer
curl -LsSf https://astral.sh/uv/install.sh | sh
curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh | sudo bash -s -- --to /usr/local/bin

# 112 threads is a trap: PyTorch grabs them all and then spends its time
# synchronising instead of computing. 16 is a sane default for this workload.
echo 'export OMP_NUM_THREADS=16' >> ~/.bashrc && source ~/.bashrc

# faster container startup: the driver stays initialised between runs
sudo nvidia-smi -pm 1
```

**Check:** `for t in docker uv just jq tmux; do command -v $t; done` lists all five.

## Step 6 — Clone the repos and make the data directories

**Where:** server, home directory

```bash
mkdir -p ~/src && cd ~/src
git clone <your-org>/itevi-mlde.git
git clone <your-org>/itevi-notes.git

mkdir -p "$ITEVI_SCRATCH"/{libraries,out,ref}
cd ~/src/itevi-mlde
chmod +x scripts/*.sh
./scripts/doctor.sh
```

**Check:** `doctor.sh` reports 0 FAIL. If it doesn't, it tells you which step
above to revisit.

**Where you now work:** `~/src/itevi-mlde` for everything, with data written to
`$ITEVI_SCRATCH`. The container sees that directory as `/scratch`.

## Step 7 — Blocker one: the real sequence

**Where:** server, `~/src/itevi-mlde`
**Why:** `config/parents/itevi.fasta` currently holds a placeholder. The code
refuses to run without a real sequence, on purpose.

```bash
cd ~/src/itevi-mlde
nano config/parents/itevi.fasta      # or edit it in VS Code over Remote-SSH
```

Use the sequence the wet-lab lead actually expresses — a UniProt entry and the
paper's construct can differ by tags and linkers.

**Check:**

```bash
PYTHONPATH=shared/src python3 - <<'CHECK'
from pathlib import Path
from itevi_core import variants
seq = variants.read_fasta(Path("config/parents/itevi.fasta"))
print("length", len(seq))
for resi, aa in [(27,"R"), (40,"H"), (75,"E")]:
    print(f"{aa}{resi}:", "OK" if seq[resi-1]==aa else f"MISMATCH -> {seq[resi-1]}")
CHECK
```

**All three must say OK.** If they don't, the numbering convention differs
(usually: the initiator Met is or isn't counted), and every Block 2 position
you use afterwards would be off by one — silently.

## Step 8 — Blocker two: the library definition

**Where:** server, `~/src/itevi-mlde`

```bash
nano config/library.yaml
```

Fill in the 10 positions that differ between I-TevI and I-BmoI. Six are named in
your handoff (30, 33, 36, 37, 38, 39); four you'll need from the paper.

**Settle position 39 first.** Your handoff says both "C39R" (C is the parent, R
is the substitution) and lists "C39" among selected residues (C is the
substitution). Those are opposite. Week 2 ranks exactly these variants, so the
wrong polarity doesn't throw an error — it just produces a mediocre-looking
result that you'd spend a week blaming on the model.

**Check:**

```bash
PYTHONPATH=shared/src python3 -c "
from pathlib import Path
from itevi_core import variants
variants.validate_parent(variants.read_fasta(Path('config/parents/itevi.fasta')),
                         variants.load_config(Path('config/library.yaml')))
print('library.yaml OK')"
```

If this isn't done today, Wednesday and Thursday still work. Week 2 doesn't.

## Step 9 — End of day

**Where:** Mac, Obsidian

Open `~/src/itevi-notes` as a vault, turn on Daily Notes with
`templates/daily.md`, install the Obsidian Git plugin with auto-commit. Five
minutes, then write today's entry.

---

# WEDNESDAY — build caching

The whole day is one lesson: **the order of lines in a Dockerfile determines
whether your rebuild takes 8 seconds or 8 minutes.** You learn it by feeling it.

## Step 10 — Build the deliberately-wrong image

**Where:** server, `~/src/itevi-mlde`
**Why:** `Dockerfile.naive` is kept in the repo permanently as the "before"
picture. It works; it's just slow in an instructive way.

Start inside tmux so a dropped connection doesn't kill a 20-minute build:

```bash
tmux new -s build
cd ~/src/itevi-mlde

time docker build -f containers/esm2-scorer/Dockerfile.naive -t esm2-naive .
```

That's roughly 15–25 minutes: ~4 GB of PyTorch wheels and 2.5 GB of model
weights. Write the time down.

*Note the trailing `.`* — the build runs from the repo root, not from the
container's folder, because the image needs `shared/` and `config/` too.

## Step 11 — Break the cache and feel it

**Where:** server, `~/src/itevi-mlde`

```bash
touch containers/esm2-scorer/src/esm2_scorer/cli.py
time docker build -f containers/esm2-scorer/Dockerfile.naive -t esm2-naive .
```

**Check:** that second build is nearly as slow as the first. A one-character
change re-downloaded 2.5 GB of weights and reinstalled PyTorch.

**Why:** Docker caches each line of a Dockerfile as a layer, and invalidates
every layer *after* the first one that changed. `Dockerfile.naive` has `COPY . .`
near the top, so touching any file invalidates everything below it — including
the expensive installs.

See it directly:

```bash
docker build -f containers/esm2-scorer/Dockerfile.naive --progress=plain -t esm2-naive . 2>&1 \
  | grep -E 'CACHED|RUN |COPY '
```

The first line without `CACHED` is where you lost.

## Step 12 — See the fix

**Where:** server, `~/src/itevi-mlde`

```bash
diff -u containers/esm2-scorer/Dockerfile.naive containers/esm2-scorer/Dockerfile
```

Two changes matter:

1. **`COPY requirements.txt` → install → `COPY src`** instead of `COPY . .` first.
   Your source changes twenty times a day; your dependencies don't. Put the stable
   thing higher.
2. **Weights downloaded by `curl` in their own layer, above the pip install.**
   Now a dependency bump doesn't re-pull 2.5 GB.

That's the entire lesson, and it generalises: **slowest and most stable at the
top, fastest and most volatile at the bottom.**

## Step 13 — Prove it

**Where:** server, `~/src/itevi-mlde`

```bash
time just esm2-build                    # cold, 15-25 min
touch containers/esm2-scorer/src/esm2_scorer/cli.py
time just esm2-build                    # after an edit
```

**Check:** the second build finishes in under 30 seconds.

Put both times, and Step 10's, in today's note. Three numbers, not six — that's
enough to make the point permanent.

---

# THURSDAY — GPU

## Step 14 — Three checks, in order

**Where:** server, `~/src/itevi-mlde`
**Why:** run them in this order and a failure tells you *which* layer broke,
instead of leaving you guessing.

```bash
cd ~/src/itevi-mlde
SHA=$(git log -1 --format=%h -- containers/esm2-scorer shared/)
IMG=itevi-mlde/esm2-scorer:$SHA

# (a) does the container get the driver?
docker run --rm --gpus all --entrypoint nvidia-smi "$IMG" -L

# (b) does PyTorch see it?  Failing HERE means a CPU-only PyTorch, not a GPU problem.
docker run --rm --gpus all --entrypoint python "$IMG" -c \
  "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"

# (c) does it work as someone else?  Nextflow will run it as a random user ID in October.
docker run --rm --user 4242:4242 "$IMG" --help
```

**Check:** (b) prints `True NVIDIA RTX A4000`, and (c) doesn't error.

## Step 15 — First real run

**Where:** server, `~/src/itevi-mlde`
**Why:** `scan` mode asks ESM-2 what it thinks about every position in the Block 2
region. It needs only the parent sequence, takes seconds, and exercises the entire
path — so it's the cheapest possible end-to-end test.

```bash
mkdir -p "$ITEVI_SCRATCH/out/esm2/scan"
docker run --rm --gpus all --user "$(id -u):$(id -g)" --shm-size 2g \
  -v "$PWD/config:/config:ro" -v "$ITEVI_SCRATCH:/scratch" \
  "$IMG" scan --output /scratch/out/esm2/scan/window.parquet --device cuda
```

The `-v` flags map directories into the container: your `config/` appears as
`/config` (read-only), and your scratch space as `/scratch`. Paths in the command
are *container* paths.

**Check — and this one is science, not plumbing.** The printed summary should
show low entropy and `parent_rank == 1` at R27, H40 and E75. That means ESM-2 is
confident those catalytic residues are what they are — evidence it has actually
learned this fold. If the active site looks like random noise, the whole Layer 1
premise is shaky, and you've learned that two weeks before the Week 3 memo was
due to tell you.

## Step 16 — Find your batch size

**Where:** server, `~/src/itevi-mlde`
**Why:** your card has 16 GB, and the default was written for 24 GB. Too big
crashes; too small wastes the GPU.

```bash
for bs in 16 32; do
  echo "== $bs"
  docker run --rm --gpus all --user "$(id -u):$(id -g)" --shm-size 2g \
    -v "$PWD/config:/config:ro" -v "$ITEVI_SCRATCH:/scratch" \
    "$IMG" scan --output "/scratch/out/esm2/scan/bs_$bs.parquet" \
    --device cuda --batch-size $bs || echo "  too big"
done
```

Watch memory in a second pane (`Ctrl-b "` then):
`watch -n1 nvidia-smi --query-gpu=memory.used,memory.total --format=csv`

**Check:** pick the larger one that leaves ~2 GB spare, and put it in the
`justfile`. Then confirm it doesn't change the answer:

```bash
python3 - <<'CHECK'
import pandas as pd, os
b = os.path.expandvars("$ITEVI_SCRATCH/out/esm2/scan")
a = pd.read_parquet(f"{b}/bs_16.parquet").set_index(["resi","aa"])["logprob"]
c = pd.read_parquet(f"{b}/bs_32.parquet").set_index(["resi","aa"])["logprob"]
print("max difference:", (a-c).abs().max())
CHECK
```

Should be around 1e-6. **Why this matters:** in October, a pipeline has to
reproduce today's numbers. If batch size changed results, that test could never
pass and you'd waste days looking for the wrong bug.

---

# FRIDAY — the deliverable

## Step 17 — Build the library, score it

**Where:** server, `~/src/itevi-mlde`
**Needs:** Step 8 complete.

```bash
tmux new -s run
cd ~/src/itevi-mlde

# expand the 10 binary positions into 2^10 = 1024 sequences
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD/config:/config:ro" -v "$ITEVI_SCRATCH:/scratch" \
  "$IMG" build-library --output /scratch/libraries/library_1024.parquet

just esm2-score library_1024 marginal     # seconds
time just esm2-score library_1024 both    # 20-40 minutes
```

**Why one is seconds and the other is 40 minutes:** all 1024 variants differ only
at 10 known positions, so the "marginal" score needs ~18 model runs total and then
every variant is a lookup. The full "pll" score runs the model once per position
per variant — 250,000 times. Both are worth having; they disagree in a way that
tells you something about the model's view of those positions interacting.

## Step 18 — Check the output

**Where:** server, `~/src/itevi-mlde`

```bash
python3 - <<'CHECK'
import pandas as pd, glob, os
f = sorted(glob.glob(os.path.expandvars("$ITEVI_SCRATCH/out/esm2/library_1024/*.parquet")))[-1]
d = pd.read_parquet(f)
print(d.shape)
print(d[["variant_id","display_name","n_mut","esm2_650m_masked_marginal"]].head())
print("NaNs:", d.filter(like="esm2_650m").isna().sum().sum())
print(d[["gpu_name","dtype","allow_tf32","image_digest"]].iloc[0])
CHECK
```

**Check:** 1024 rows, no NaNs. `image_digest` reads `unset` — correct, because
nothing has been pushed to a registry yet. That happens in Week 2.

## Step 19 — Send the data contract

**Where:** anywhere
**Why:** this is the longest-lead item in the whole week. The answers take days
to come back and Week 3 can't freeze without them. Draft it in the gaps while
builds run.

`docs/data-contract-v0-skeleton.md` has the full list. The two questions that
will hurt most if unasked:

- **Does the CE readout saturate?** If it's "fraction cleaved at a fixed
  timepoint", it can't distinguish good from very good — which is exactly the
  discrimination the Week 19 optimisation needs.
- **Are replicates delivered individually or pre-averaged?** Pre-averaged
  destroys the error model you need in Week 17, and you can't recover it later.

## Step 20 — Close the week

- Three build times and your batch size in the week's notes
- Skim `docs/reference/AUDIT-week1-scalability.md`'s MED list — those are due before Week 3
- Optional if there's time: `just esm2-build-cpu` gives a CPU version that scores
  the same library in a minute on your 112 threads, as an independent
  cross-check. Nice to have, not required. It's also what will run on AWS Fargate
  in Week 5.

---

## Lost?

**Where am I and what state is it in:**

```bash
pwd                          # should usually be ~/src/itevi-mlde
git -C ~/src/itevi-mlde status
docker images | grep esm2    # what have I built
ls "$ITEVI_SCRATCH"/out/esm2 # what have I produced
```

**Common confusions:**

| Symptom | Cause |
|---|---|
| `permission denied ... docker.sock` | Not in the docker group yet — `newgrp docker` |
| `could not select device driver` | Step 4 incomplete, or dockerd not restarted |
| `nvidia-smi` works but PyTorch says False | CPU-only PyTorch in the image, not a GPU problem |
| Build re-downloads 2.5 GB every time | You're using `Dockerfile.naive` — that's the point of it |
| `no space left on device` but `df /` looks fine | Docker's storage is elsewhere — `docker system df` |
| "file not found" inside the container | You used a host path where a container path is needed |

**If you need the why:** `docs/reference/environments.md` §1–2 explains containers and the
GPU stack; §10 is a bigger failure table. Don't read it cover to cover.

---

## What I deliberately left out of this week

So you know these are decisions, not oversights:

- **ECR, S3, AWS anything** — Week 2 and later.
- **Nextflow, Terraform** — Weeks 6+.
- **The CPU image** — optional Friday extra.
- **Image-size optimisation experiments** — interesting, not load-bearing.
- **The position-5 homolog search** — Week 4 background task.
- **Thread-count benchmarking** — set 16 and move on; revisit if something's slow.

One thing worth starting anyway, because it's slow and Week 2 waits on it: the
GIY-YIG MSA build. Start it in tmux and walk away.

```bash
tmux new -s msa
# jackhmmer -N 5 --cpu 96 -A giy_yig.sto <parent.fasta> <uniref90.fasta>
```
