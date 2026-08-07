# itevi-mlde

ML-guided directed evolution of I-TevI nuclease cleavage specificity.
Computational campaign infrastructure: Layer 1 zero-shot scoring, Layer 2
cleavage-profile prediction, Layer 3 Pareto acquisition.

**Start here:**

| Doc | What |
|---|---|
| `docs/conventions.md` | Naming, IDs, storage layout. Read before adding anything. |
| `docs/environments.md` | How the Mac / GPU-server split works and why. |
| `docs/RUNBOOK-server-setup.md` | Copy-paste server build-out with verification gates. |
| `docs/AUDIT-week1-scalability.md` | What was fixed in Week 1 and what is still dated. |
| `containers/esm2-scorer/DESIGN.md` | The first container's contract. |

The learning log and portfolio write-ups live in a **separate** public-safe repo
(`<username>-mlde-learning`), not here. See `docs/conventions.md` §1.

```bash
./scripts/doctor.sh      # verify this host
just --list              # everything else
```
