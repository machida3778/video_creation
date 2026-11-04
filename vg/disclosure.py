"""Shared disclosure rule for manifest assets and publishing metadata."""
import re

from .schemas import Manifest

REALISTIC_PROMPT = re.compile(r"photo|photoreal|realistic|cinematic photo", re.I)


def realistic_assets(manifest: Manifest) -> list[str]:
    return [a.id for a in manifest.assets if a.kind in ("image", "video") and (
        a.params.get("realistic") is True or REALISTIC_PROMPT.search(str(a.params.get("prompt", ""))))]


def needs_disclosure(manifest: Manifest) -> bool:
    return bool(realistic_assets(manifest))
