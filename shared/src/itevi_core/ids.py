"""Canonical variant identity for the whole campaign.

THE PROBLEM THIS SOLVES
-----------------------
The obvious variant ID is the mutation string: "R30K_E36D". It works for the
1024-member binary library and fails everywhere else:

  * Layer 4 generates 10K-100K designs from ProteinMPNN/LigandMPNN. Those are
    not describable as a short mutation list from any parent -- they may differ
    at every position in Block 2.
  * The position-5 homolog search (Week 4) may introduce a DIFFERENT GIY-YIG
    parent. "R30K" is meaningless without knowing which parent it is relative to.
  * A 10-mutation variant gets a 40-character ID that becomes a filename.
  * Two different generators can produce the same sequence under two names,
    and you silently score it twice and average it into the training set.

THE RULE
--------
The canonical ID is content-addressed: derived from the amino-acid sequence and
nothing else. Same sequence, same ID, forever, regardless of who made it or how.
Human-readable names are ATTRIBUTES, not identity.

This is the join key between Layer 1 features, wet-lab assay results, and Layer 2
training rows. Get it wrong and the whole campaign's data model has a soft spot.
"""

from __future__ import annotations

import hashlib

ID_PREFIX = "v"
ID_HEX_LEN = 12  # 48 bits; collision probability over 1e6 variants is ~1e-6


def sequence_sha256(seq: str) -> str:
    """Full hash of the uppercased amino-acid sequence."""
    return hashlib.sha256(seq.strip().upper().encode()).hexdigest()


def variant_id(seq: str) -> str:
    """Canonical, content-addressed variant ID. Stable across tools and years."""
    return f"{ID_PREFIX}_{sequence_sha256(seq)[:ID_HEX_LEN]}"


def display_name(parent_seq: str, seq: str, max_muts: int = 6) -> str:
    """Human-readable label. An ATTRIBUTE, never a key.

    Falls back to the canonical ID when the variant is too far from the parent
    to describe as a mutation list -- which is the normal case for Layer 4.
    """
    if len(parent_seq) != len(seq):
        return variant_id(seq)
    muts = [f"{p}{i + 1}{v}" for i, (p, v) in enumerate(zip(parent_seq, seq)) if p != v]
    if not muts:
        return "parent"
    if len(muts) > max_muts:
        return f"{variant_id(seq)}[{len(muts)}mut]"
    return "_".join(muts)
