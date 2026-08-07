"""esm2-score CLI: scan | build-library | score."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import sys
import time
from pathlib import Path

import pandas as pd
import torch

from itevi_core import ids, provenance as prov, variants

from . import scoring

DTYPES = {"float32": torch.float32, "bfloat16": torch.bfloat16, "float16": torch.float16}

EXIT_BAD_INPUT = 2
EXIT_NO_DEVICE = 3
EXIT_OOM = 4


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _weights_sha() -> str:
    cache = Path(os.environ.get("TORCH_HOME", "~/.cache/torch")).expanduser()
    ckpt = cache / "hub" / "checkpoints" / "esm2_t33_650M_UR50D.pt"
    return _file_sha256(ckpt) if ckpt.exists() else "unknown"


MODEL = "esm2_t33_650M_UR50D"
FEATURE_PREFIX = "esm2_650m"   # {tool}_{model}_{metric} -- see docs/conventions.md §3


def provenance(args, device: str, parent_seq: str) -> dict:
    """Tool-specific provenance plus the shared runtime block.

    `parent_sha256` matters because Week 4's homolog search may introduce a second
    GIY-YIG parent. A delta score is meaningless without recording what it was a
    delta FROM.
    """
    import esm

    return {
        "model_name": MODEL,
        "parent_sha256": ids.sequence_sha256(parent_seq),
        "model_sha256": _weights_sha(),
        "fair_esm_version": getattr(esm, "__version__", "2.0.0"),
        "torch_version": torch.__version__,
        "device": device,
        "cuda_version": torch.version.cuda or "n/a",
        "gpu_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "n/a",
        "dtype": args.dtype,
        # Recorded because TF32 silently changes results by ~1e-3 on Ampere+.
        # If two hosts disagree, check this column before hunting for a bug.
        "allow_tf32": bool(torch.backends.cuda.matmul.allow_tf32),
        **prov.base_provenance(),
    }


def _resolve_device(args) -> str:
    device = scoring.pick_device(args.device)
    if args.device == "cuda" and not torch.cuda.is_available():
        raise SystemExit(EXIT_NO_DEVICE)
    if args.device == "mps" and not torch.backends.mps.is_available():
        raise SystemExit(EXIT_NO_DEVICE)
    print(f"[device] {device} (requested: {args.device})", file=sys.stderr)
    return device


def cmd_scan(args) -> int:
    """Per-position log-prob matrix over the analysis window. ~18 forward passes.

    Runs without a completed library.yaml `positions` block -- deliberately, so
    the window profile is available in Week 1 before the paper's position list
    is reconciled.
    """
    parent = variants.read_fasta(Path(args.parent))
    cfg = variants.load_config(Path(args.config))
    variants.validate_parent(parent, cfg, require_positions=False)
    window = variants.window_indices(cfg, len(parent))

    device = _resolve_device(args)
    model, alphabet = scoring.load_model(device, DTYPES[args.dtype])

    t0 = time.time()
    table = scoring.masked_logprob_table(model, alphabet, parent, window, device, args.batch_size)
    rows = scoring.window_profile(table, parent, alphabet)
    elapsed = time.time() - t0

    out = pd.DataFrame(rows)
    for k, v in provenance(args, device, parent).items():
        out[k] = v
    out.to_parquet(args.output, index=False)

    summary = (
        out.drop_duplicates("resi")[["resi", "parent_aa", "position_entropy_frac_max", "parent_rank"]]
        .sort_values("resi")
    )
    print(f"\n[scan] {len(window)} positions in {elapsed:.1f}s\n", file=sys.stderr)
    print(summary.to_string(index=False), file=sys.stderr)
    print(
        "\nSanity check: catalytic positions should show LOW entropy_frac_max and "
        "parent_rank == 1. If they do not, ESM-2 has not learned this active site.\n",
        file=sys.stderr,
    )
    return 0


def cmd_build_library(args) -> int:
    parent = variants.read_fasta(Path(args.parent))
    cfg = variants.load_config(Path(args.config))
    variants.validate_parent(parent, cfg)
    positions = [variants.Position(**p) for p in cfg["positions"]]
    df = pd.DataFrame(variants.build_library(parent, positions))
    df.to_parquet(args.output, index=False)
    print(f"wrote {len(df)} variants to {args.output}", file=sys.stderr)
    return 0


def cmd_score(args) -> int:
    parent = variants.read_fasta(Path(args.parent))
    cfg = variants.load_config(Path(args.config))
    variants.validate_parent(parent, cfg, require_positions=False)
    window = variants.window_indices(cfg, len(parent))

    df = (
        pd.read_parquet(args.input)
        if str(args.input).endswith(".parquet")
        else pd.read_csv(args.input)
    )
    if df["variant_id"].duplicated().any():
        print("duplicate variant_id in input", file=sys.stderr)
        return EXIT_BAD_INPUT
    if args.limit:
        df = df.head(args.limit)
        print(f"[limit] scoring first {len(df)} rows only", file=sys.stderr)

    observed = sorted({i for s in df["sequence"] for i, (a, b) in enumerate(zip(parent, s)) if a != b})
    outside = [p + 1 for p in observed if p not in window]
    if outside:
        print(
            f"input varies at positions outside the analysis window: {outside}. "
            "Widen analysis_window or fix the input.",
            file=sys.stderr,
        )
        return EXIT_BAD_INPUT
    print(f"[window] scoring over {window[0]+1}-{window[-1]+1}; "
          f"input varies at {[p+1 for p in observed]}", file=sys.stderr)

    device = _resolve_device(args)
    model, alphabet = scoring.load_model(device, DTYPES[args.dtype])

    masked_table = scoring.masked_logprob_table(model, alphabet, parent, window, device, args.batch_size)
    parent_table = scoring.parent_logprob_table(model, alphabet, parent, device)

    parent_pll = float("nan")
    if args.mode in ("pll", "both"):
        t0 = time.time()
        parent_pll = scoring.pseudo_log_likelihood(model, alphabet, parent, device, args.batch_size)
        per = time.time() - t0
        print(
            f"[pll] {per:.1f}s per variant -> ~{per * len(df) / 60:.0f} min for {len(df)}. "
            "Ctrl-C now if that is not what you signed up for.",
            file=sys.stderr,
        )

    records = []
    for n, row in enumerate(df.itertuples(), 1):
        n_mut, mutstr = variants.diff_from_parent(parent, row.sequence)
        rec = {
            # Recompute rather than trust the input: a mismatch means the table
            # was hand-edited and the join key no longer matches the sequence.
            "variant_id": ids.variant_id(row.sequence),
            "sequence_sha256": ids.sequence_sha256(row.sequence),
            "display_name": ids.display_name(parent, row.sequence),
            "n_mut": n_mut,
            "mutations": mutstr,
            f"{FEATURE_PREFIX}_masked_marginal": scoring.marginal_score(
                masked_table, parent, row.sequence, alphabet, window
            ),
            f"{FEATURE_PREFIX}_parent_marginal": scoring.marginal_score(
                parent_table, parent, row.sequence, alphabet, window
            ),
        }
        if args.mode in ("pll", "both"):
            pll = scoring.pseudo_log_likelihood(
                model, alphabet, row.sequence, device, args.batch_size
            )
            rec[f"{FEATURE_PREFIX}_pll"] = pll
            rec[f"{FEATURE_PREFIX}_pll_delta"] = pll - parent_pll
            if n % 25 == 0:
                print(f"  {n}/{len(df)}", file=sys.stderr)
        records.append(rec)

    out = pd.DataFrame.from_records(records)
    out["window_start"] = window[0] + 1
    out["window_end"] = window[-1] + 1
    for k, v in provenance(args, device, parent).items():
        out[k] = v
    out.to_parquet(args.output, index=False)
    print(f"wrote {len(out)} rows to {args.output}", file=sys.stderr)
    return 0


def _common(p):
    p.add_argument("--parent", default="/config/parents/itevi.fasta")
    p.add_argument("--config", default="/config/library.yaml")
    p.add_argument("--device", choices=["auto", "cuda", "mps", "cpu"], default="auto")
    p.add_argument("--dtype", choices=list(DTYPES), default="float32")
    p.add_argument("--batch-size", type=int, default=8)


def main() -> int:
    ap = argparse.ArgumentParser(prog="esm2-score")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s1 = sub.add_parser("scan", help="per-position log-prob matrix over the window")
    _common(s1)
    s1.add_argument("--output", required=True)
    s1.set_defaults(func=cmd_scan)

    s2 = sub.add_parser("build-library", help="expand the binary combinatorial library")
    s2.add_argument("--parent", default="/config/parents/itevi.fasta")
    s2.add_argument("--config", default="/config/library.yaml")
    s2.add_argument("--output", required=True)
    s2.set_defaults(func=cmd_build_library)

    s3 = sub.add_parser("score", help="score a variant table")
    _common(s3)
    s3.add_argument("--input", required=True)
    s3.add_argument("--output", required=True)
    s3.add_argument("--mode", choices=["marginal", "pll", "both"], default="marginal")
    s3.add_argument("--limit", type=int, default=0, help="score first N rows (timing probe)")
    s3.set_defaults(func=cmd_score)

    args = ap.parse_args()
    try:
        return args.func(args)
    except variants.LibraryError as e:
        print(f"input error: {e}", file=sys.stderr)
        return EXIT_BAD_INPUT
    except torch.cuda.OutOfMemoryError:
        print("CUDA OOM -- retry with a lower --batch-size", file=sys.stderr)
        return EXIT_OOM


if __name__ == "__main__":
    sys.exit(main())
