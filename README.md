# itevi-mlde

ML-guided directed evolution of I-TevI nuclease cleavage specificity.
Layer 1 zero-shot scoring → Layer 2 cleavage-profile prediction → Layer 3 Pareto
acquisition.

## Start here

**→ [`WEEK-1.md`](WEEK-1.md)** — the only file you need this week. Follow it top
to bottom.

## Everything else

| Doc | Read when |
|---|---|
| `docs/conventions.md` | Before adding a new file or container. Naming, IDs, where things live. |
| `docs/data-contract-v0-skeleton.md` | Drafting the wet-lab data contract. |
| `containers/esm2-scorer/DESIGN.md` | You need the container's exact input/output contract. |
| `docs/reference/environments.md` | Something GPU- or container-related confuses you. §10 is a failure table. |
| `docs/reference/RUNBOOK-server-setup.md` | Setting up a *second* server, or you want the long-form version of Week 1. |
| `docs/reference/AUDIT-week1-scalability.md` | Historical. Skim the MED list before Week 3. |

Working notes go in a separate private vault, `itevi-notes` — see
`docs/conventions.md` §1.

## Layout

```
containers/     one directory per Layer 1 tool
shared/         itevi-core: variant identity, provenance
config/         parent sequences, library and panel definitions
docs/           this documentation
scripts/        host setup and verification
```

Data lives in `$ITEVI_SCRATCH`, never in the repo.

```bash
./scripts/doctor.sh    # is this host ready?
just --list            # available commands
```
