import os
import shutil

# Create directories
os.makedirs('modules/media_assets', exist_ok=True)
os.makedirs('modules/integrations', exist_ok=True)

# 1. Write modules/media_assets/models.py
models_code = """from __future__ import annotations

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
"""
with open('modules/media_assets/models.py', 'w', encoding='utf-8') as f:
    f.write(models_code)

# 2. Move files
files_to_move = [
    ('modules/tierlists/asset_repository.py', 'modules/media_assets/asset_repository.py'),
    ('modules/tierlists/assets.py', 'modules/media_assets/assets.py'),
    ('modules/tierlists/database.py', 'modules/media_assets/database.py'),
    ('modules/tierlists/downloads.py', 'modules/media_assets/downloads.py'),
    ('modules/tierlists/exceptions.py', 'modules/media_assets/exceptions.py'),
    ('modules/tierlists/repository_utils.py', 'modules/media_assets/repository_utils.py'),
]

for src, dst in files_to_move:
    if os.path.exists(src):
        shutil.move(src, dst)
        print(f"Moved {src} to {dst}")

# 3. Move integrations
if os.path.exists('modules/tierlists/integrations'):
    for item in os.listdir('modules/tierlists/integrations'):
        if item != '__pycache__':
            src = os.path.join('modules/tierlists/integrations', item)
            dst = os.path.join('modules/integrations', item)
            shutil.move(src, dst)
            print(f"Moved {src} to {dst}")

# 4. Remove directories
if os.path.exists('modules/tierlists'):
    shutil.rmtree('modules/tierlists')
    print("Deleted modules/tierlists")

if os.path.exists('cogs/tierlist_templates'):
    shutil.rmtree('cogs/tierlist_templates')
    print("Deleted cogs/tierlist_templates")
