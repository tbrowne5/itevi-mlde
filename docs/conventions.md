# Conventions

Decisions that are cheap now and expensive in October. Each one exists because
something downstream in the 24-week plan breaks without it.

---

## 1. Repositories — there are two

### `itevi-mlde` — the project monorepo (private)

The name is not arbitrary: the plan's Week 6 resource-tagging convention is
`Project=itevi-mlde`, so the repo, the AWS tag, the ECR namespace, and the S3
prefix all use the same string. One name, everywhere.

```
itevi-mlde/
├── containers/              # one dir per Layer 1 tool
│   ├── esm2-scorer/         # W1
│   ├── evmutation-scorer/   # W2
│   ├── proteinmpnn-scorer/  # W3
│   ├── ligandmpnn-scorer/   # W3
│   └── esmfold-runner/      # W9, anchor-only
├── shared/                  # itevi-core: identity, provenance, schemas
├── config/                  # library definitions, parent sequences, panels
├── schemas/                 # Pandera schemas (W3, W11)
├── pipelines/               # featurize.nf (W10), train_predict.nf (W19)
├── infra/                   # Terraform modules + envs (W8-12)
├── ml/                      # Layer 2/3 training, serving (W11-20)
├── docs/
├── scripts/
└── justfile                 # delegates to per-container justfiles
```

**Why a monorepo and not five container repos.** The cross-cutting changes in
this project are the common case, not the exception: freezing the data contract
in Week 3 touches the output schema of every container, the Pandera schema, and
the Nextflow channel definitions. In five repos that is five coordinated PRs and
a version-compatibility matrix maintained by one person. In one repo it is one
commit. The usual argument for splitting — independent release cadence across
teams — does not apply to a solo computational lead.

### `<username>-mlde-learning` — the portfolio repo (public-safe)

This is the repo the Week 0 plan names, and it is **not** the project repo.

- `learning-log.md`, daily entries from Week 1
- write-ups, diagrams, notes, the Week 21 portfolio pass
- sanitised snippets, never proprietary sequences or unpublished results

**Why separate.** The project brief requires all IP be clean for commercial use,
and Week 21 schedules a public write-up "if IP allows". If the learning log lives
inside the private repo, publishing means auditing and scrubbing a year of
history under time pressure. Separate from day one, and the question never
arises. Link the two by date, never by import.

---

## 2. Variant identity

**The canonical ID is content-addressed:** `v_<first 12 hex of sha256(sequence)>`.

Mutation strings like `R30K_E36D` are a *display name*, an attribute, never a
key. Three reasons, in increasing order of how much they would hurt:

1. A 10-mutation variant produces a 40-character ID that ends up as a filename.
2. Week 4's position-5 homolog search may introduce a different GIY-YIG parent.
   `R30K` is meaningless without knowing which parent it is relative to — and the
   mutation string does not carry that.
3. **Layer 4 breaks it outright.** ProteinMPNN and LigandMPNN generate 10K–100K
   designs that may differ at every position in Block 2. There is no short
   mutation list. Any ID scheme that assumes "small edit from a known parent"
   fails the moment Round 3 starts.

Content addressing also gives deduplication for free: two generators producing
the same sequence get the same ID, so you cannot silently score it twice and
average it into the training set as if it were independent evidence.

`shared/src/itevi_core/ids.py` is the single implementation. Every container and
every ML script imports it. The ID is the join key between Layer 1 features,
wet-lab assay rows, and Layer 2 training data.

---

## 3. Feature column naming

`{tool}_{model}_{metric}` — e.g. `esm2_650m_masked_marginal`.

The model token is not decoration. Week 16 evaluates frozen ESM-2 embeddings as
features and the plan explicitly expects no gain; you will want to compare model
sizes, and possibly ESM-C or a newer PLM. A bare `esm2_masked_marginal` column
collides the moment a second model is scored, and Week 10's merged Parquet is
where that collision surfaces.

Reserved prefixes, fixed now so the merge in Week 10 is mechanical:

| Prefix | Source |
|---|---|
| `esm2_650m_` | ESM-2 650M |
| `evmut_` | EVmutation / EVcouplings |
| `pmpnn_` | ProteinMPNN (protein-only graph) |
| `lmpnn_` | LigandMPNN (protein + DNA graph) |
| `esmfold_` | ESMFold, anchor use only |
| `phys_` | computed physics features (W15) |
| `assay_` | wet-lab measurements |
| `pred_` | model predictions |

Non-prefixed columns are reserved for identity and provenance.

---

## 4. Image tagging

**Do not tag with `git rev-parse HEAD` in a monorepo.** A Terraform commit would
change the sha, retag every container, and destroy the claim that a given tag
corresponds to a given behaviour. Use a **path-scoped** sha:

```bash
git log -1 --format=%h -- containers/esm2-scorer shared/
```

That only moves when the container or its shared dependency actually changes.

| | Value |
|---|---|
| local tag | `esm2-scorer:<path-scoped-sha>` |
| ECR repo | `<acct>.dkr.ecr.<region>.amazonaws.com/itevi-mlde/esm2-scorer` |
| ECR tag | same path-scoped sha, **immutable tags on** |
| pipeline reference | digest, never tag (W3 onward) |

**`IMAGE_DIGEST` must be a runtime env var, not a build arg.** The digest is
assigned by the registry on push, so at build time it does not exist. Passing it
as `--build-arg` yields either a confident-looking `unset` or, worse, a stale
value from a previous build baked into the layer. The `justfile` resolves it with
`docker inspect` and injects `-e IMAGE_DIGEST=...` at run time.

---

## 5. Storage layout

### Local (server)

```
~/src/itevi-mlde/              # code only
$ITEVI_SCRATCH/                # /scratch/<user>/itevi
  ├── ref/                     # parent seqs, MSA, structures — large, static
  ├── work/                    # Nextflow work dir (W7+)
  └── out/{tool}/{library}/    # container outputs
```

### S3 — decide now, not in Week 4

```
s3://itevi-mlde-<acct>-<region>/
  ├── ref/                     # MSA, structures, parent sequences
  ├── libraries/{library_id}/  # variant tables (id, sequence)
  ├── features/{tool}/{library_id}/{run_id}.parquet
  ├── assay/{round}/           # wet-lab intake, post-Pandera
  ├── models/{model_id}/
  └── scratch/                 # 7-day lifecycle expiry (plan, W6)
```

`features/` partitioned by tool then library makes the Week 10 merge a single
Athena/Glue query rather than a directory walk, which is exactly what the Week
11–14 data-engineering block assumes.

**Region: pick one and write it down.** The plan leaves us-east-1/us-east-2 open.
The lab AWS account arrives in Week 6; if it differs from your personal account's
region you will pay cross-region transfer on every dataset move and hit
inconsistent service availability. Decide before Week 4.

---

## 6. Config, not code

- `config/parents/*.fasta` — one file per parent scaffold. **Not** `itevi_wt.fasta`.
  Week 4's homolog search may add non-I-TevI parents, at which point "WT" is
  ambiguous. The CLI flag is `--parent`, and the parent's own hash is recorded in
  provenance, so a row always knows what it was compared against.
- `config/library.yaml` — a *generator* for the binary Tev/Bmo library, not the
  canonical input. The canonical input to every scorer is a variant table
  (`variant_id`, `sequence`). Generators produce tables; scorers consume tables.
  Layer 4 will emit tables from a completely different route, and nothing
  downstream should need to know the difference.
- `config/panels/*.yaml` — substrate panel definitions. IDs are **additive and
  never renumbered**; the panel goes 28 → hundreds during the campaign, and any
  renumbering silently invalidates every prior assay row.
- Config is **mounted, not baked**. The image ships a default copy at `/config`
  so it runs standalone, but `-v $PWD/config:/config:ro` wins. Otherwise a
  one-line panel change costs a rebuild and a new digest.

---

## 7. Model weights: bake or fetch

| Weights size | Policy |
|---|---|
| < 3 GB | bake into the image (ESM-2 650M, 2.5 GB) |
| ≥ 3 GB | fetch from S3 at startup into a cached volume |

Image size drives Batch job latency directly — every task pulls the image, and
the plan already flags Fargate cold-start as a Week 5 concern. An 8 GB image is
tolerable; ESMFold and Boltz-1 stacked on top of it are not. Decide per container
at build time, and record which policy was used in `DESIGN.md`.

---

## 8. Output file naming

```
{tool}/{library_id}/{run_id}.parquet
```

Never `esm2_scores_1024.parquet`. The run ID is in the data as well as the path,
so a file separated from its directory is still self-describing — which matters
the first time someone emails you a Parquet.

---

## 9. What is deliberately deferred

Recorded so that revisiting is a decision rather than a discovery:

- **Per-container Python versions.** Everything is 3.11 until a tool forces
  otherwise. LigandMPNN is the likely first exception.
- **A real package registry for `itevi-core`.** Path install from the monorepo is
  fine while one person builds everything. Revisit if a second person joins.
- **Multi-arch image builds.** No production consumer needs arm64. The Mac uses
  `Dockerfile.cpu` natively; that is not the same thing as a published manifest.
- **Nextflow `-profile` for the lab account.** Week 6, when access exists.
