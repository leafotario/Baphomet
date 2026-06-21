import os
import re

replacements = [
    (r'from cogs\.tierlist_templates\.asset_repository import', r'from modules.tierlists.asset_repository import'),
    (r'from cogs\.tierlist_templates\.assets import', r'from modules.tierlists.assets import'),
    (r'from cogs\.tierlist_templates\.database import', r'from modules.tierlists.database import'),
    (r'from cogs\.tierlist_templates\.downloads import', r'from modules.tierlists.downloads import'),
    (r'from cogs\.tierlist_templates\.exceptions import', r'from modules.tierlists.exceptions import'),
    (r'from cogs\.tierlist_templates\.repository import', r'from modules.tierlists.repository import'),
    (r'from cogs\.tierlist_templates\.repository_utils import', r'from modules.tierlists.repository_utils import'),
    
    (r'from cogs\.tierlist_wikipedia\.wikipedia import', r'from modules.tierlists.integrations.wikipedia import'),
    (r'from cogs\.tierlist_spotify\.spotify import', r'from modules.tierlists.integrations.spotify import'),
    
    (r'from cogs\.vinculos_rendering\.renderer import', r'from modules.vinculos.rendering.renderer import'),
    (r'from cogs\.vinculos_rendering\.drawing import', r'from modules.vinculos.rendering.drawing import'),
    (r'from cogs\.vinculos_rendering\.fonts import', r'from modules.vinculos.rendering.fonts import'),
    
    (r'from cogs\.iceberg\.sources\.providers import', r'from modules.iceberg.sources.providers import'),
    
    (r'from cogs\.xp\.rendering\.', r'from modules.xp.rendering.'),
    (r'from cogs\.xp\.utils\.', r'from modules.xp.utils.'),
    (r'from cogs\.xp\.db\.', r'from modules.xp.db.'),
    (r'from cogs\.xp\.rank_badges import', r'from modules.xp.rendering.rank_badges import'),
    (r'from cogs\.xp\.rank_config import', r'from modules.xp.services.rank_config import'),
]

def process_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original = content
    for old, new in replacements:
        content = re.sub(old, new, content)
        
    if original != content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Fixed: {filepath}")

for root, _, files in os.walk('.'):
    if '.git' in root or '.venv' in root or '__pycache__' in root:
        continue
    for f in files:
        if f.endswith('.py'):
            process_file(os.path.join(root, f))
