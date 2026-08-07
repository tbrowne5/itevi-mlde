# Data contract v0 — CE cleavage assay → Layer 2

Draft. Circulate to wet-lab lead Week 1, review Week 2, freeze Week 3.

The contract is not a file format. It is the set of questions that, unanswered,
will silently break Layer 2 in November. Each section below is one of them.

## 1. Unit of measurement
What number comes off the CE instrument per (variant, substrate)?
Fraction cleaved at a fixed timepoint? Initial rate? Pseudo-first-order k_obs?
Fraction-cleaved-at-t saturates, and a saturating readout cannot distinguish
"good" from "very good" — which is exactly the discrimination EHVI needs in W19.
Ask now, not in Round 1.

## 2. Substrate panel
The 28 substrates: canonical IDs, 5-mer motif, full oligo sequence, fluorophore,
which are on-target and which are the off-target set for the specificity ratio.
Panel changes over the campaign are guaranteed (W3 onward expansion track) —
so IDs must be additive, never renumbered.

## 3. Replicate structure
Technical vs biological replicates. Are replicates delivered individually or
pre-averaged? Pre-averaged kills the W17 noise model. Ask for individuals.

## 4. Error model
Per-measurement CV, or a variance estimate. This is the input to noise-aware
training and to propagated uncertainty on the log-space specificity ratio.
If the lab cannot give a CV, the deliverable is a replicate study, not a guess.

## 5. Censoring and the "dead" definition
Limit of detection: below what value is a reading "no cleavage" rather than a
small number? Is it left-censored or reported as zero? The W13 hurdle model's
classifier target is literally this definition — get it in writing.

## 6. Batch metadata
Plate/run ID, date, operator, instrument, oligo lot. Without these you cannot
detect or correct batch effects, and you will attribute a plate artifact to a
mutation.

## 7. Controls
WT I-TevI on every plate, every substrate — non-negotiable, it is the
normalizer. Plus a catalytically dead negative (R27A or E75A).

## 8. Delivery
Format (CSV/Parquet), column names and dtypes, where it lands (S3 prefix),
who writes it, cadence. Pandera schema is written against this in Week 3.

## 9. Identity
How does a well map to a variant? Sequence-verified? What is the authoritative
variant ID, and does it match the one Layer 1 uses? Join on sequence hash, not
on a human-typed name.
