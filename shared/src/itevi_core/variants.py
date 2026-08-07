"""Library construction, analysis window, and WT validation."""

from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass
from pathlib import Path

import yaml

CANONICAL_AA = set("ACDEFGHIKLMNPQRSTVWY")


class LibraryError(RuntimeError):
    """Any inconsistency between config and WT sequence. Exit code 2."""


@dataclass(frozen=True)
class Position:
    resi: int  # 1-indexed
    tev: str
    bmo: str

    @property
    def idx0(self) -> int:
        return self.resi - 1


def read_fasta(path: Path) -> str:
    lines = path.read_text().strip().splitlines()
    if not lines or not lines[0].startswith(">"):
        raise LibraryError(f"{path} is not a FASTA file")
    seq = "".join(l.strip() for l in lines[1:] if not l.startswith(">")).upper()
    bad = set(seq) - CANONICAL_AA
    if bad:
        raise LibraryError(f"non-canonical residues in {path}: {sorted(bad)}")
    if not seq:
        raise LibraryError(f"{path} contains no sequence")
    return seq


def load_config(path: Path) -> dict:
    cfg = yaml.safe_load(path.read_text())
    for key in ("positions", "fixed", "analysis_window"):
        if key not in cfg:
            raise LibraryError(f"{path} missing required key: {key}")
    return cfg


def window_indices(cfg: dict, seq_len: int) -> list[int]:
    """0-indexed positions in the analysis window."""
    w = cfg["analysis_window"]
    start, end = int(w["start"]), int(w["end"])
    if start < 1 or end > seq_len or start > end:
        raise LibraryError(
            f"analysis_window {start}-{end} invalid for sequence of length {seq_len}"
        )
    return list(range(start - 1, end))


def validate_parent(seq: str, cfg: dict, require_positions: bool = True) -> None:
    """Fail loudly on numbering mismatch before anything expensive runs."""
    for f in cfg["fixed"]:
        resi, aa = f["resi"], f["aa"]
        if resi > len(seq):
            raise LibraryError(f"fixed residue {resi} beyond sequence length {len(seq)}")
        if seq[resi - 1] != aa:
            raise LibraryError(
                f"numbering mismatch: expected {aa}{resi}, found {seq[resi - 1]}{resi}. "
                "Check whether the FASTA includes the initiator Met, or whether the "
                "paper uses a different numbering convention. Every window and Block 2 "
                "index downstream depends on this being right."
            )

    if not require_positions:
        return

    win = cfg["analysis_window"]
    for p in cfg["positions"]:
        if p["resi"] == 0 or "?" in (p["tev"], p["bmo"]):
            raise LibraryError(
                f"library.yaml still has placeholders at {p}. Fill in from the paper. "
                "(`scan` runs without this; `build-library` and `score` do not.)"
            )
        if seq[p["resi"] - 1] != p["tev"]:
            raise LibraryError(
                f"position {p['resi']}: config says Tev residue is {p['tev']}, "
                f"WT FASTA has {seq[p['resi'] - 1]}"
            )
        if not win["start"] <= p["resi"] <= win["end"]:
            raise LibraryError(
                f"mutable position {p['resi']} is outside the analysis window "
                f"{win['start']}-{win['end']}"
            )
        for f in cfg["fixed"]:
            if f["resi"] == p["resi"]:
                raise LibraryError(
                    f"position {p['resi']} is listed as both mutable and fixed "
                    f"({f.get('role', 'fixed')}). Resolve before building the library."
                )


def build_library(parent: str, positions: list[Position]) -> list[dict]:
    """Expand the 2^n binary combinatorial library into a canonical variant table.

    Emits the campaign-wide schema: content-addressed `variant_id` (see ids.py),
    with the mutation string demoted to `display_name`. Every generator in this
    project -- this one, and the Layer 4 MPNN samplers in Round 3 -- must emit
    these same three columns and nothing generator-specific.
    """
    from . import ids

    n = len(positions)
    out = []
    for combo in itertools.product([0, 1], repeat=n):
        seq = list(parent)
        for bit, p in zip(combo, positions):
            if bit:
                seq[p.idx0] = p.bmo
        seq = "".join(seq)
        out.append(
            {
                "variant_id": ids.variant_id(seq),
                "sequence": seq,
                "display_name": ids.display_name(parent, seq),
                "sequence_sha256": ids.sequence_sha256(seq),
                "n_mut": sum(combo),
                "generator": "block2_binary_tev_bmo",
            }
        )
    if len({r["variant_id"] for r in out}) != 2**n:
        raise LibraryError(
            "duplicate sequences in the expansion -- two position entries likely "
            "share a resi, or tev == bmo somewhere in library.yaml"
        )
    return out


def sha256(seq: str) -> str:
    return hashlib.sha256(seq.encode()).hexdigest()


def diff_from_parent(parent: str, seq: str) -> tuple[int, str]:
    if len(parent) != len(seq):
        raise LibraryError("variant length differs from parent; indels are not supported")
    muts = [f"{w}{i + 1}{v}" for i, (w, v) in enumerate(zip(parent, seq)) if w != v]
    return len(muts), ";".join(muts)
