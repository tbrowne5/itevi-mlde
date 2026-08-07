# Audit — will the Week 1 scaffold survive to Week 24?

Ran against the flat `esm2-scorer/` scaffold on 2026-08-07, checking every fixed
choice (names, paths, IDs, tags) against what Weeks 2–24 of the plan require.
Fourteen findings. Five are fixed in this commit; the rest are dated.

Severity: **HIGH** = fix before the first push. **MED** = fix by the Week 3 data
contract freeze. **LOW** = decide before the week named.

---

## HIGH — fixed now

### H1. `esm2-scorer` was masquerading as a repository

It is one of five Layer 1 containers (`esm2`, `evmutation`, `proteinmpnn`,
`ligandmpnn`, `esmfold-runner`), and the project also needs Nextflow pipelines,
Terraform, Layer 2/3 ML code, and schemas. Five container repos plus infra plus
ML is eight repos for one person, and the plan's most common change type — the
Week 3 data-contract freeze — touches most of them simultaneously.

**Fixed:** monorepo `itevi-mlde`, containers under `containers/`. Name taken from
the plan's own Week 6 tagging convention (`Project=itevi-mlde`) so repo, ECR
namespace, S3 bucket, and AWS tags share one string.

### H2. `learning-log.md` had nowhere to live that was publishable

The plan wants a learning repo (Week 0) and a public write-up (Week 21) "if IP
allows", while the project brief requires clean commercial IP. A log inside the
private repo means scrubbing a year of history under deadline.

**Fixed:** two repos. `itevi-mlde` (private, project) and
`<username>-mlde-learning` (public-safe, portfolio). Linked by date, never by
import.

### H3. `IMAGE_DIGEST` as a build arg is not merely wrong, it is impossible

```dockerfile
ARG IMAGE_DIGEST=unset          # <-- the digest does not exist yet
ENV IMAGE_DIGEST=$IMAGE_DIGEST
```

A registry assigns the digest when the image is **pushed**. At build time there
is nothing to pass. The failure mode is quiet: every row says `unset`, or worse
carries a stale digest copied from a previous build, and the provenance column
that Week 10's reproduction test depends on is confidently wrong.

**Fixed:** runtime injection. `shared/itevi_core/provenance.py` reads the env var;
the `justfile` resolves the real digest with `docker inspect` and passes
`-e IMAGE_DIGEST=...` on `docker run`.

### H4. Git-sha image tags break in a monorepo

`docker build -t esm2-scorer:$(git rev-parse --short HEAD)` was fine in a
single-purpose repo. In a monorepo, a Terraform commit changes HEAD and therefore
retags the container — so identical images get different tags, and "this tag
means this behaviour" stops being true exactly when Week 10 needs it.

**Fixed:** path-scoped sha —
`git log -1 --format=%h -- containers/esm2-scorer shared/`. Moves only when the
container or its shared dependency actually changes.

### H5. `variant_id` scheme does not survive Layer 4

IDs were mutation strings (`R30K_E36D`). This fails three ways:

- **Layer 4 kills it outright.** ProteinMPNN/LigandMPNN generate 10K–100K designs
  differing at potentially every Block 2 position. There is no short mutation
  list, so the scheme has no output for the majority of candidates the campaign
  will eventually score.
- **Week 4 makes it ambiguous.** The position-5 homolog search may introduce a
  different GIY-YIG parent, and `R30K` does not say which parent it is relative
  to.
- **No deduplication.** Two generators can emit the same sequence under different
  names; you score it twice and average it into training as independent evidence.

**Fixed:** content-addressed IDs, `v_<12 hex of sha256(sequence)>`, in
`shared/itevi_core/ids.py`. Mutation strings demoted to a `display_name`
attribute that degrades gracefully (`v_a1b2c3d4e5f6[14mut]`) when a variant is
too far from any parent to describe.

This is the join key between Layer 1 features, assay results, and Layer 2
training rows. It was the single most expensive thing in the audit to have got
wrong.

---

## MED — fix by the Week 3 contract freeze

### M1. Feature columns don't encode the model

`esm2_masked_marginal` collides the moment a second PLM is scored — and Week 16
explicitly evaluates ESM-2 embeddings as features, which invites comparing model
sizes. Convention is now `{tool}_{model}_{metric}`, e.g.
`esm2_650m_masked_marginal`, with a reserved prefix table in `conventions.md` §3.
*Rename before any data is written that Layer 2 will train on.*

### M2. "WT" is the wrong abstraction

`--wt`, `itevi_wt.fasta`, `wt_marginal`. Week 4 may introduce non-I-TevI GIY-YIG
parents, at which point "wild type" is ambiguous. Renamed to `--parent`, with
`config/parents/itevi.fasta` and the parent's own hash recorded in provenance so
every row knows what it was compared against. *Column rename tracks M1.*

### M3. `library.yaml` was treated as canonical input

It encodes the binary Tev/Bmo structure, which describes exactly one of the
libraries this campaign will score. It is a **generator**; the canonical input to
every scorer is a variant table (`variant_id`, `sequence`). Layer 4 emits tables
by a completely different route and nothing downstream should care.

### M4. No substrate panel schema exists

The data contract skeleton asks the right questions but there is no
`config/panels/*.yaml`. The panel goes 28 → hundreds (Week 3 onward, wet-lab
led). Needs additive, never-renumbered IDs — renumbering silently invalidates
every prior assay row, and nothing would raise an error.

### M5. No S3 prefix convention

Weeks 4–5 will invent one under time pressure, and Weeks 11–14 assume a layout
that Athena/Glue can query. Proposed layout in `conventions.md` §5. *Decide
before Week 4.*

### M6. Weights baked unconditionally

Correct for ESM-2 (2.5 GB). Wrong for ESMFold (Week 9) and Boltz-1, where image
pull time starts dominating Batch task duration — and the plan already flags
Fargate cold-start as a Week 5 concern. Policy now stated: bake under 3 GB, fetch
from S3 above it, recorded per container in `DESIGN.md`.

### M7. Config baked into the image

`COPY config /config` means a one-line panel change costs a rebuild and a new
digest. Now: baked as a default so the image runs standalone, but
`-v $PWD/config:/config:ro` overrides.

---

## LOW — decide before the week named

### L1. AWS region undecided (before Week 4)

The plan says "us-east-1/us-east-2". The lab account arrives Week 6; a mismatch
between it and your personal account means cross-region transfer charges on every
dataset move and inconsistent service availability. Pick one, put it in `.envrc`.

### L2. Output filenames (before Week 10)

`esm2_scores_1024.parquet` does not survive multiple runs, libraries, or tools.
Now `{tool}/{library_id}/{run_id}.parquet`, with the run ID also inside the file
so a Parquet separated from its directory is still self-describing.

---

## Non-findings — checked and fine

- **`/data`, `/work`, `/config` mount points.** Conventional, stable, and
  Nextflow-compatible. No change.
- **Exit code scheme (2/3/4).** Already what Week 9's `errorStrategy` and Batch
  retry need.
- **Provenance columns on every row rather than in sidecar metadata.** Verbose,
  and correct — Parquet compresses the repetition to nearly nothing, and a row
  that gets copied into another table keeps its lineage.
- **`sequence_sha256` already present.** It was there as a column; the fix in H5
  was promoting it to *identity* rather than adding it.
- **Pinned `fair-esm==2.0.0` and explicit dtype.** Exactly what the Week 10
  reproduction test requires.

---

## What this cost

About an hour of restructuring in Week 1. The same changes in Week 10 would mean
re-scoring every library to regenerate IDs, rewriting the Pandera schema after it
was frozen, and reconciling assay rows already joined on the old key — with wet-
lab data arriving on a schedule you do not control.

The Week 3 contract freeze is the real deadline. After it, `variant_id` and the
feature column names are load-bearing for everything downstream.
