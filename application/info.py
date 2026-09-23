"""Model catalog shim for documents/pdf2text (expects ``info.get_model_info``).

``application/`` is on sys.path during Documents sync, so this module is imported
as a top-level ``info`` the same way as in agentic-work / cde-pilot.
"""

from __future__ import annotations

from typing import Any, Optional

import models
import utils


def get_model_info(model_name: Optional[str] = None) -> list[dict[str, Any]]:
    """Return a one-element profile list compatible with pdf2text vision chat."""
    profile = models.get_model_profile(model_name)
    cfg = utils.load_config()
    region = (
        str(profile.get("bedrock_region") or "").strip()
        or str(cfg.get("region") or "").strip()
        or "us-west-2"
    )
    out = dict(profile)
    out["bedrock_region"] = region
    return [out]
