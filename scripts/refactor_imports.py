import os
import re

def replace_in_file(filepath, replacements):
    if not os.path.exists(filepath):
        return
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    new_content = content
    for pattern, repl in replacements:
        new_content = re.sub(pattern, repl, new_content)
        
    if new_content != content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"Updated: {filepath}")

def main():
    # 1. CORE IMPORTS GLOBALLY
    core_replacements = [
        (r'from core.logger import', r'from core.logger import'),
        (r'import core.logger as core_logger', r'import core.logger as core_logger'),
        (r'from core.db_transaction import', r'from core.db_transaction import'),
        (r'import core.db_transaction as core_db_transaction', r'import core.db_transaction as core_db_transaction'),
        (r'from core.redis_state import', r'from core.redis_state import'),
        (r'import core.redis_state as core_redis_state', r'import core.redis_state as core_redis_state'),
        (r'from core.database import', r'from core.database import'),
        (r'import database\n', r'from core import database\n'),
        (r'from utils.occult_ui import', r'from utils.occult_ui import')
    ]
    for root, _, files in os.walk('.'):
        for f in files:
            if f.endswith('.py'):
                replace_in_file(os.path.join(root, f), core_replacements)

    # 2. ICEBERG
    iceberg_replacements = [
        (r'from \.models import', r'from modules.iceberg.models import'),
        (r'from \.service import', r'from modules.iceberg.service import'),
        (r'from \.repository import', r'from modules.iceberg.repository import'),
        (r'from \.renderer import', r'from modules.iceberg.renderer import'),
        (r'from \.themes import', r'from modules.iceberg.themes import'),
        (r'from \.constants import', r'from modules.iceberg.constants import'),
        (r'from \.modals import', r'from modules.iceberg.discord_ui.modals import'),
        (r'from \.views import', r'from modules.iceberg.discord_ui.views import')
    ]
    replace_in_file('cogs/iceberg/commands.py', iceberg_replacements)
    for root, _, files in os.walk('modules/iceberg'):
        for f in files:
            replace_in_file(os.path.join(root, f), iceberg_replacements)

    # 3. TIERLISTS
    tierlist_replacements = [
        (r'from \.database import', r'from modules.tierlists.database import'),
        (r'from \.migrations import', r'from modules.tierlists.migrations import'),
        (r'from \.models import', r'from modules.tierlists.models import'),
        (r'from \.asset_repository import', r'from modules.tierlists.asset_repository import'),
        (r'from \.template_repository import', r'from modules.tierlists.template_repository import'),
        (r'from \.session_repository import', r'from modules.tierlists.session_repository import'),
        (r'from \.repository_utils import', r'from modules.tierlists.repository_utils import'),
        (r'from \.renderer import', r'from modules.tierlists.renderer import'),
        (r'from \.session_renderer import', r'from modules.tierlists.session_renderer import'),
        (r'from \.item_resolver import', r'from modules.tierlists.item_resolver import'),
        (r'from \.downloads import', r'from modules.tierlists.downloads import'),
        (r'from \.exceptions import', r'from modules.tierlists.exceptions import'),
        (r'from \.messages import', r'from modules.tierlists.messages import'),
        (r'from \.session_views import', r'from modules.tierlists.ui.session_views import'),
        (r'from \.dynamic_items import', r'from modules.tierlists.ui.dynamic_items import'),
        (r'from \.views import', r'from modules.tierlists.ui.views import'),
        (r'from \.modals import', r'from modules.tierlists.ui.modals import')
    ]
    replace_in_file('cogs/tierlist_templates/commands.py', tierlist_replacements)
    for root, _, files in os.walk('modules/tierlists'):
        for f in files:
            replace_in_file(os.path.join(root, f), tierlist_replacements)

    # 4. WHITETEXT
    whitetext_replacements = [
        (r'from \.layout import', r'from modules.media_processing.whitetext.layout import'),
        (r'from \.processor import', r'from modules.media_processing.whitetext.processor import'),
        (r'from \.video import', r'from modules.media_processing.whitetext.video import'),
        (r'from \.errors import', r'from modules.media_processing.whitetext.errors import')
    ]
    replace_in_file('cogs/whitetext/commands.py', whitetext_replacements)
    for root, _, files in os.walk('modules/media_processing/whitetext'):
        for f in files:
            replace_in_file(os.path.join(root, f), whitetext_replacements)

    # 5. FADEIN_IMG
    fadein_replacements = [
        (r'from \.processor import', r'from modules.media_processing.fadein_img.processor import'),
        (r'from \.errors import', r'from modules.media_processing.fadein_img.errors import')
    ]
    replace_in_file('cogs/fadein_img/commands.py', fadein_replacements)
    for root, _, files in os.walk('modules/media_processing/fadein_img'):
        for f in files:
            replace_in_file(os.path.join(root, f), fadein_replacements)

    # 6. VINCULOS
    vinc_replacements = [
        (r'from vinculos_rendering\.drawing import', r'from modules.vinculos.rendering.drawing import'),
        (r'from vinculos_rendering\.fonts import', r'from modules.vinculos.rendering.fonts import'),
        (r'from vinculos_rendering\.renderer import', r'from modules.vinculos.rendering.renderer import'),
        (r'from \.vinculos_rendering\.drawing import', r'from modules.vinculos.rendering.drawing import'),
        (r'from \.vinculos_rendering\.fonts import', r'from modules.vinculos.rendering.fonts import'),
        (r'from \.vinculos_rendering\.renderer import', r'from modules.vinculos.rendering.renderer import'),
    ]
    replace_in_file('cogs/vinculos.py', vinc_replacements)
    
    # 7. XP SYSTEM
    xp_replacements = [
        (r'from \.xp_models import', r'from modules.xp.db.xp_models import'),
        (r'from \.xp_repository import', r'from modules.xp.db.xp_repository import'),
        (r'from \.xp_migrations import', r'from modules.xp.db.xp_migrations import'),
        (r'from \.xp_cards import', r'from modules.xp.rendering.xp_cards import'),
        (r'from \.xp_service import', r'from modules.xp.services.service import'),
        (r'from \.xp_config import', r'from modules.xp.services.config import'),
        (r'from \.xp_views import', r'from modules.xp.services.views import'),
        (r'from \.rank_badges import', r'from modules.xp.rendering.rank_badges import'),
        (r'from \.xp_constants import', r'from modules.xp.services.constants import'),
        (r'from \.xp_runtime import', r'from modules.xp.services.runtime import')
    ]
    replace_in_file('cogs/xp/commands.py', xp_replacements)
    for root, _, files in os.walk('modules/xp'):
        for f in files:
            if f.endswith('.py'):
                replace_in_file(os.path.join(root, f), xp_replacements)

if __name__ == '__main__':
    main()
