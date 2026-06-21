import os
import re

replacements = [
    (r'from modules\.tierlists\.asset_repository import', r'from modules.media_assets.asset_repository import'),
    (r'from modules\.tierlists\.assets import', r'from modules.media_assets.assets import'),
    (r'from modules\.tierlists\.database import', r'from modules.media_assets.database import'),
    (r'from modules\.tierlists\.downloads import', r'from modules.media_assets.downloads import'),
    (r'from modules\.tierlists\.exceptions import', r'from modules.media_assets.exceptions import'),
    (r'from modules\.tierlists\.repository_utils import', r'from modules.media_assets.repository_utils import'),
    (r'from modules\.tierlists\.models import', r'from modules.media_assets.models import'),
    
    (r'from modules\.tierlists\.integrations\.wikipedia import', r'from modules.integrations.wikipedia import'),
    (r'from modules\.tierlists\.integrations\.spotify import', r'from modules.integrations.spotify import'),
    (r'from modules\.tierlists\.integrations\.safety import', r'from modules.integrations.safety import'),
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
