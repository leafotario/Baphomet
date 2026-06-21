import os
import shutil

moves = [
    # Tierlist Templates Leftovers
    ("cogs/tierlist_templates/assets.py", "modules/tierlists/assets.py"),
    ("cogs/tierlist_templates/repository.py", "modules/tierlists/repository.py"),
    ("cogs/tierlist_templates/repository_utils.py", "modules/tierlists/repository_utils.py"),
    ("cogs/tierlist_templates/asset_repository.py", "modules/tierlists/asset_repository.py"),
    ("cogs/tierlist_templates/database.py", "modules/tierlists/database.py"),
    ("cogs/tierlist_templates/downloads.py", "modules/tierlists/downloads.py"),
    ("cogs/tierlist_templates/exceptions.py", "modules/tierlists/exceptions.py"),
    ("cogs/tierlist_templates/item_resolver.py", "modules/tierlists/item_resolver.py"),
    ("cogs/tierlist_templates/messages.py", "modules/tierlists/messages.py"),
    ("cogs/tierlist_templates/migrations.py", "modules/tierlists/migrations.py"),
    ("cogs/tierlist_templates/models.py", "modules/tierlists/models.py"),
    ("cogs/tierlist_templates/session_renderer.py", "modules/tierlists/session_renderer.py"),
    ("cogs/tierlist_templates/session_repository.py", "modules/tierlists/session_repository.py"),
    ("cogs/tierlist_templates/template_repository.py", "modules/tierlists/template_repository.py"),

    ("cogs/tierlist_templates/dynamic_items.py", "modules/tierlists/ui/dynamic_items.py"),
    ("cogs/tierlist_templates/modals.py", "modules/tierlists/ui/modals.py"),
    ("cogs/tierlist_templates/session_views.py", "modules/tierlists/ui/session_views.py"),
    ("cogs/tierlist_templates/views.py", "modules/tierlists/ui/views.py"),
    
    # Tierlist Wikipedia Leftovers
    ("cogs/tierlist_wikipedia/safety.py", "modules/tierlists/integrations/safety.py"),
    ("cogs/tierlist_wikipedia/wikipedia.py", "modules/tierlists/integrations/wikipedia.py"),
    
    # Tierlist Spotify Leftovers
    ("cogs/tierlist_spotify/spotify.py", "modules/tierlists/integrations/spotify.py"),
    
    # Vinculos Rendering Leftovers
    ("cogs/vinculos_rendering/drawing.py", "modules/vinculos/rendering/drawing.py"),
    ("cogs/vinculos_rendering/fonts.py", "modules/vinculos/rendering/fonts.py"),
    ("cogs/vinculos_rendering/renderer.py", "modules/vinculos/rendering/renderer.py"),
    
    # Iceberg Leftovers
    ("cogs/iceberg/sources/providers.py", "modules/iceberg/sources/providers.py"),
    ("cogs/iceberg/constants.py", "modules/iceberg/constants.py"),
    ("cogs/iceberg/models.py", "modules/iceberg/models.py"),
    ("cogs/iceberg/renderer.py", "modules/iceberg/renderer.py"),
    ("cogs/iceberg/repository.py", "modules/iceberg/repository.py"),
    ("cogs/iceberg/service.py", "modules/iceberg/service.py"),
    ("cogs/iceberg/themes.py", "modules/iceberg/themes.py"),
    ("cogs/iceberg/modals.py", "modules/iceberg/discord_ui/modals.py"),
    ("cogs/iceberg/views.py", "modules/iceberg/discord_ui/views.py"),
    
    # Whitetext Leftovers
    ("cogs/whitetext/errors.py", "modules/media_processing/whitetext/errors.py"),
    ("cogs/whitetext/layout.py", "modules/media_processing/whitetext/layout.py"),
    ("cogs/whitetext/processor.py", "modules/media_processing/whitetext/processor.py"),
    ("cogs/whitetext/video.py", "modules/media_processing/whitetext/video.py"),
    
    # FadeIn Img Leftovers
    ("cogs/fadein_img/errors.py", "modules/media_processing/fadein_img/errors.py"),
    ("cogs/fadein_img/processor.py", "modules/media_processing/fadein_img/processor.py"),
    
    # XP System Leftovers (nested in cogs/xp/)
    ("cogs/xp/db/xp_migrations.py", "modules/xp/db/xp_migrations.py"),
    ("cogs/xp/db/xp_models.py", "modules/xp/db/xp_models.py"),
    ("cogs/xp/db/xp_repository.py", "modules/xp/db/xp_repository.py"),
    
    ("cogs/xp/rendering/xp_card_renderer.py", "modules/xp/rendering/xp_card_renderer.py"),
    ("cogs/xp/rendering/xp_views.py", "modules/xp/rendering/xp_views.py"),
    ("cogs/xp/rank_badges.py", "modules/xp/rendering/rank_badges.py"),
    
    ("cogs/xp/utils/xp_curves.py", "modules/xp/utils/xp_curves.py"),
    ("cogs/xp/utils/xp_models.py", "modules/xp/utils/xp_models.py"),
    ("cogs/xp/utils/xp_text.py", "modules/xp/utils/xp_text.py"),
    ("cogs/xp/utils/xp_vinculos.py", "modules/xp/utils/xp_vinculos.py"),
    
    ("cogs/xp/xp_constants.py", "modules/xp/services/xp_constants.py"),
    ("cogs/xp/xp_runtime.py", "modules/xp/services/xp_runtime.py"),
    ("cogs/xp/xp_service.py", "modules/xp/services/xp_service.py"),
    ("cogs/xp/rank_config.py", "modules/xp/services/rank_config.py"),
]

for src, dst in moves:
    if os.path.exists(src):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        print(f"Moved {src} -> {dst}")

# Cleanup empty directories and unneeded __init__ files
dirs_to_clean = [
    "cogs/tierlist_templates",
    "cogs/tierlist_wikipedia",
    "cogs/tierlist_spotify",
    "cogs/vinculos_rendering",
    "cogs/iceberg/sources",
    "cogs/iceberg",
    "cogs/whitetext",
    "cogs/fadein_img",
    "cogs/xp/db",
    "cogs/xp/rendering",
    "cogs/xp/utils",
    "cogs/xp",
]

for d in dirs_to_clean:
    if os.path.exists(d):
        init_file = os.path.join(d, "__init__.py")
        if os.path.exists(init_file):
            os.remove(init_file)
            print(f"Deleted {init_file}")

print("Movement completed.")
