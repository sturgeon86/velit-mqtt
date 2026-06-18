"""Small shared helpers."""

from __future__ import annotations

import re


def slugify(value: str) -> str:
    """Turn an arbitrary name into an MQTT/topic-safe identifier.

    Lowercases, replaces any run of non-alphanumeric characters with a single
    underscore, and trims leading/trailing underscores. Used to derive a node
    id from a device's friendly name (falling back to its address).
    """
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug
