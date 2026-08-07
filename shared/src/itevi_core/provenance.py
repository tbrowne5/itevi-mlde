"""Provenance stamped into every Layer 1 output row.

IMAGE_DIGEST is read from the ENVIRONMENT AT RUN TIME, never from a build arg.
A registry assigns the digest when the image is pushed, so at build time it does
not exist. Passing it as --build-arg produces a confident-looking "unset" or, far
worse, a stale value copied from a previous build.

Runtime injection instead. See config/conventions -- the justfile resolves the
digest with `docker inspect` and passes -e IMAGE_DIGEST=...
"""

from __future__ import annotations

import datetime as dt
import os
import uuid


def base_provenance() -> dict:
    return {
        "image_digest": os.environ.get("IMAGE_DIGEST", "unset"),
        "image_tag": os.environ.get("IMAGE_TAG", "unset"),
        "git_sha": os.environ.get("GIT_SHA", "unset"),
        "run_id": os.environ.get("RUN_ID", str(uuid.uuid4())),
        "utc_timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
