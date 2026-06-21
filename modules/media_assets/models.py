from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

@dataclass(frozen=True)
class TierAsset:
    id: str
    asset_hash: str
    storage_path: str
    mime_type: str
    width: int
    height: int
    size_bytes: int
    source_type: str | None
    metadata: dict[str, Any]
    created_at: str
    marked_orphan_at: str | None
    deleted_at: str | None

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
