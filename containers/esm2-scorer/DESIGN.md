# esm2-scorer — DESIGN

Status: v0 (Week 1). Owner: computational lead.

## Purpose

Score I-TevI variants with ESM-2 (650M) and emit a Layer 1 zero-shot feature table.
One job of the four-tool Layer 1 set; the first container built, and the template the
other three (`evmutation-scorer`, `proteinmpnn-scorer`, `ligandmpnn-scorer`) copy.

## Non-goals

- No structure prediction, no DNA awareness. ESM-2 sees protein sequence only.
  Anything requiring the DNA complex is LigandMPNN's job (Week 3).
- No fine-tuning. Ever, on this project — ~1000 measurements will overfit a PLM.
- No orchestration. This container reads a file and writes a file. Nextflow wires
  it up in Week 10.

## Inputs

CSV or Parquet with columns:

| column      | type | notes                                                        |
|-------------|------|--------------------------------------------------------------|
| `variant_id`| str  | unique, stable across the whole campaign                      |
| `sequence`  | str  | full-length AA sequence, uppercase, no gaps                   |

Optionally generated instead from `config/library.yaml` + the WT FASTA via
`esm2-score build-library`, which is how the 1024-member binary library is made.

## Outputs

Parquet, one row per variant:

| column                  | meaning                                                            |
|-------------------------|--------------------------------------------------------------------|
| `variant_id`            | passthrough                                                         |
| `sequence_sha256`       | hash of the AA sequence — the real join key                         |
| `n_mut`                 | Hamming distance from WT                                            |
| `mutations`             | `C39R;E36K` style, 1-indexed, WT-first                              |
| `esm2_masked_marginal`  | sum over mutated sites of log p(mut) - log p(wt), WT context, masked |
| `esm2_wt_marginal`      | same, single unmasked forward pass (cheap sanity check)             |
| `esm2_pll`              | full pseudo-log-likelihood, sum over all L positions                |
| `esm2_pll_delta`        | `esm2_pll` - `esm2_pll(WT)`                                         |

Plus a provenance block, written to every row (verbose, deliberately):
`model_name`, `model_sha256`, `fair_esm_version`, `torch_version`, `cuda_version`,
`dtype`, `image_digest`, `git_sha`, `run_id`, `utc_timestamp`.

### Why both marginal and PLL

`masked_marginal` is the ESM-1v standard and is site-additive — it assumes the 10
Block 2 positions are independent. `pll` does not. On a library where every variant
carries up to 10 mutations in one 16-residue block, the gap between them is the
model's view of epistasis in that block. Report both; the disagreement is a result,
not a bug.

## Compute shape

- Masked marginals over a *fixed* mutable position set cost 10 forward passes total,
  not 1024 — mask each Block 2 position once in WT context, then every variant score
  is a table lookup. Seconds.
- Full PLL costs L forward passes per variant: 1024 x 245 = ~250k. ~20-40 min on an
  A10G at batch 32. This is the only expensive mode.
- Memory: 650M params fp32 = 2.6 GB weights; batch 32 at L=247 peaks around 8-10 GB.
  Comfortable on g5.xlarge (24 GB).

## Reproducibility constraints (non-negotiable)

The Week 10 pipeline test must reproduce the Week 3 manual spike numerically. That
fails for boring reasons unless these are pinned now:

- `dtype` is fp32 by default and recorded in the output. fp16/bf16 shift PLL sums in
  the third decimal. Do not switch dtype mid-campaign without re-running the anchor.
- `fair-esm==2.0.0`, torch pinned, model file checksummed at load.
- Batch size must not change the result. It does not for scoring, but the test that
  proves it (`--batch-size 1` vs `32` on 10 variants) is in `tests/`.

## Failure modes

| condition                          | exit | behaviour                        |
|------------------------------------|------|----------------------------------|
| WT sanity assertion fails          | 2    | refuse to run                    |
| input has duplicate `variant_id`   | 2    | refuse to run                    |
| sequence contains non-canonical AA | 2    | refuse to run                    |
| CUDA unavailable and `--device cuda`| 3   | refuse (do not silently fall back)|
| OOM                                | 4    | suggest lower `--batch-size`     |

Silent CPU fallback is banned. A 40-minute GPU job that quietly becomes a 14-hour CPU
job is the failure you find out about on Friday afternoon.

## Open questions (resolve before Week 2)

1. **Position 39 polarity.** Handoff summary says both "C39R" (C is WT) and lists
   "C39" among selected residues (C is the substitution). Resolve against Loedige
   supplementary. Encode in `config/library.yaml`, not in prose.
2. **The other 4 of 10 differing positions.** Six are named (30, 33, 36, 37, 38, 39).
   The library is 2^10. Fill in the remaining four from the paper.
3. **WT numbering offset.** Does residue 1 include the initiator Met? The assertion
   `WT[26]=='R' and WT[39]=='H' and WT[74]=='E'` (0-indexed) catches an off-by-one
   against the stated catalytic core R27/H40/E75. If it fails, the numbering
   convention differs and every Block 2 index is wrong.
