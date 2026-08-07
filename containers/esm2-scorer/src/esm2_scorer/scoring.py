"""ESM-2 zero-shot scoring.

Modes:
  scan             per-position log-prob matrix over the analysis window (18 fwd passes)
  masked marginal  sum over mutated sites of log p(mut) - log p(parent)  (window fwd passes)
  parent marginal  same, single unmasked forward pass
  pll              full pseudo-log-likelihood, L forward passes PER VARIANT

Token indexing: the ESM alphabet prepends BOS, so residue i (0-indexed) is at
token position i + 1. Getting this wrong shifts every score by one residue and
produces plausible-looking garbage.
"""

from __future__ import annotations

import math

import torch

CANONICAL_AA = "ACDEFGHIKLMNPQRSTVWY"


def pick_device(requested: str) -> str:
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def load_model(device: str, dtype: torch.dtype, allow_tf32: bool = False):
    """Load ESM-2 650M onto `device`.

    TF32 is OFF by default, deliberately. On Ampere and later (the RTX A4000 is
    compute capability 8.6), cuDNN's TF32 path is enabled by default and truncates
    matmul mantissas from 23 bits to 10. Invisible for training, fine for ranking,
    but it puts a ~1e-3 floor on host-to-host agreement -- and ~1e-6 is the
    tolerance the Week 10 pipeline test is checked against. Off, recorded in the
    output provenance, and changed only on purpose.
    """
    import esm

    torch.backends.cuda.matmul.allow_tf32 = allow_tf32
    torch.backends.cudnn.allow_tf32 = allow_tf32

    if device == "mps" and dtype is not torch.float32:
        raise ValueError(
            "MPS with non-fp32 is not trustworthy for this model; several reduction "
            "ops silently degrade. Use --dtype float32 on Apple Silicon."
        )
    model, alphabet = esm.pretrained.esm2_t33_650M_UR50D()
    model = model.to(device=device, dtype=dtype)
    model.eval()
    return model, alphabet


@torch.inference_mode()
def masked_logprob_table(
    model, alphabet, parent: str, positions: list[int], device: str, batch_size: int = 8
) -> dict[int, torch.Tensor]:
    """log p(aa | WT context, position masked) for each 0-indexed position.

    This is the whole trick for a fixed-window combinatorial library:
    len(positions) forward passes gives every variant's masked-marginal score
    by table lookup. For an 18-residue window that is 18 passes for all 1024
    variants -- a laptop CPU job, not a GPU job.
    """
    bc = alphabet.get_batch_converter()
    _, _, toks = bc([("parent", parent)])
    toks = toks.to(device)

    table: dict[int, torch.Tensor] = {}
    for start in range(0, len(positions), batch_size):
        chunk = positions[start : start + batch_size]
        batch = toks.repeat(len(chunk), 1).clone()
        for row, p in enumerate(chunk):
            batch[row, p + 1] = alphabet.mask_idx
        logits = model(batch)["logits"]
        for row, p in enumerate(chunk):
            table[p] = torch.log_softmax(logits[row, p + 1].float(), dim=-1).cpu()
    return table


@torch.inference_mode()
def parent_logprob_table(model, alphabet, parent: str, device: str) -> torch.Tensor:
    """Single unmasked forward pass. Cheap sanity check, not a substitute."""
    bc = alphabet.get_batch_converter()
    _, _, toks = bc([("parent", parent)])
    logits = model(toks.to(device))["logits"]
    return torch.log_softmax(logits[0].float(), dim=-1).cpu()


def window_profile(table: dict[int, torch.Tensor], parent: str, alphabet) -> list[dict]:
    """Long-format rows: one per (position, amino acid), plus per-position stats.

    Per-position entropy over the 20 canonical residues is the conservation
    proxy. The Week 3 memo check: entropy at the catalytic positions should be
    visibly lower than at the Block 2 specificity positions. If it is not,
    ESM-2 has not learned this active site and Layer 1 is on sand.
    """
    aa_idx = [alphabet.get_idx(a) for a in CANONICAL_AA]
    rows = []
    for p in sorted(table):
        lp = table[p]
        canon = lp[aa_idx]
        renorm = torch.log_softmax(canon, dim=-1)
        probs = renorm.exp()
        entropy = float(-(probs * renorm).sum())
        parent_lp = float(lp[alphabet.get_idx(parent[p])])
        order = torch.argsort(canon, descending=True)
        parent_rank = int((order == CANONICAL_AA.index(parent[p])).nonzero()[0]) + 1
        for j, aa in enumerate(CANONICAL_AA):
            rows.append(
                {
                    "resi": p + 1,
                    "parent_aa": parent[p],
                    "mut_aa": aa,
                    "logprob": float(lp[alphabet.get_idx(aa)]),
                    "logprob_delta_vs_wt": float(lp[alphabet.get_idx(aa)]) - parent_lp,
                    "position_entropy_nats": entropy,
                    "position_entropy_frac_max": entropy / math.log(20),
                    "parent_logprob": parent_lp,
                    "parent_rank": parent_rank,
                }
            )
    return rows


def marginal_score(table, parent: str, seq: str, alphabet, positions: list[int]) -> float:
    total = 0.0
    for p in positions:
        if seq[p] == parent[p]:
            continue
        lp = table[p] if isinstance(table, dict) else table[p + 1]
        total += float(lp[alphabet.get_idx(seq[p])] - lp[alphabet.get_idx(parent[p])])
    return total


@torch.inference_mode()
def pseudo_log_likelihood(
    model, alphabet, seq: str, device: str, batch_size: int = 32
) -> float:
    """Full PLL: mask each position in turn, sum log p(true token).

    L forward passes per variant. Makes no site-independence assumption, unlike
    masked marginals. This is the only expensive mode -- run it on the server.
    """
    bc = alphabet.get_batch_converter()
    _, _, toks = bc([("s", seq)])
    L = len(seq)
    total = 0.0

    for start in range(0, L, batch_size):
        idxs = list(range(start, min(start + batch_size, L)))
        batch = toks.repeat(len(idxs), 1).clone()
        for row, p in enumerate(idxs):
            batch[row, p + 1] = alphabet.mask_idx
        logits = model(batch.to(device))["logits"]
        for row, p in enumerate(idxs):
            lp = torch.log_softmax(logits[row, p + 1].float(), dim=-1)
            total += float(lp[toks[0, p + 1]])
    return total
