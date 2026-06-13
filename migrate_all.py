import os
import re

# ========================================================
# RANK CONFIG MIGRATION
# ========================================================
rank_file = "cogs/xp/commands/rank_admin_commands.py"
with open(rank_file, "r") as f:
    content = f.read()

content = content.replace("class RankAdminCommands(commands.Cog):", 'class RankConfig(app_commands.GroupCog, group_name="rank_config", group_description="Configurações do sistema de rank."):')
content = content.replace('rank_insignia = app_commands.Group(\n        name="rank-insignia",', 'insignia = app_commands.Group(\n        name="insignia",')
content = content.replace("@rank_insignia.command", "@insignia.command")
content = content.replace('name="rank_levelrole_set"', 'name="levelrole_set"')
content = content.replace('name="rank_levelrole_remove"', 'name="levelrole_remove"')
content = content.replace('name="rank_levelrole_list"', 'name="levelrole_list"')

with open("cogs/xp/commands/rank_config.py", "w") as f:
    f.write(content)

os.remove(rank_file)

# Update inits
cmd_init = "cogs/xp/commands/__init__.py"
with open(cmd_init, "r") as f:
    c = f.read()
c = c.replace("from .rank_admin_commands import RankAdminCommands", "from .rank_config import RankConfig")
c = c.replace('"RankAdminCommands",', '"RankConfig",')
with open(cmd_init, "w") as f:
    f.write(c)

xp_init = "cogs/xp/__init__.py"
with open(xp_init, "r") as f:
    c = f.read()
c = c.replace("RankAdminCommands", "RankConfig")
with open(xp_init, "w") as f:
    f.write(c)

# ========================================================
# DENUNCIA MIGRATION
# ========================================================
denuncia_views_and_commands = """
# ─────────────────────────────────────────────
#  VIEWS DE DENÚNCIA
# ─────────────────────────────────────────────
ROLE_STAFF_ID = 1393546293418397866

class BotaoAbrirDenuncia(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Abrir denúncia", emoji="🚨", custom_id="denuncia:abrir")
    async def abrir(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        membro = interaction.user
        role_staff = guild.get_role(ROLE_STAFF_ID)

        nome_canal = f"denuncia-{membro.name}".lower().replace(" ", "-")[:40]
        canal_existente = discord.utils.get(guild.text_channels, name=nome_canal)
        if canal_existente:
            await interaction.response.send_message(f"❌ Você já tem um ticket aberto em {canal_existente.mention}.", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            membro: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        if role_staff:
            overwrites[role_staff] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)

        canal = await guild.create_text_channel(
            name=nome_canal, overwrites=overwrites, category=interaction.channel.category, reason=f"Ticket de denúncia aberto por {membro}"
        )
        await canal.send(content=f"Denúncia aberta <@&{ROLE_STAFF_ID}>", view=BotaoFecharDenuncia())
        await interaction.response.send_message(f"✅ Seu ticket foi aberto em {canal.mention}.", ephemeral=True)

class BotaoFecharDenuncia(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Fechar denúncia", emoji="🔒", style=discord.ButtonStyle.secondary, custom_id="denuncia:fechar")
    async def fechar(self, interaction: discord.Interaction, button: discord.ui.Button):
        role_staff = interaction.guild.get_role(ROLE_STAFF_ID)
        eh_staff = role_staff in interaction.user.roles if role_staff else False
        eh_admin = interaction.user.guild_permissions.administrator

        if not (eh_staff or eh_admin):
            await interaction.response.send_message("❌ Apenas a staff pode fechar denúncias.", ephemeral=True)
            return

        await interaction.response.send_message("🔒 Fechando o ticket...", ephemeral=True)
        await interaction.channel.delete(reason=f"Ticket fechado por {interaction.user}")

"""

# Add commands into ServerConfig
with open("cogs/server_config.py", "r") as f:
    sc = f.read()

# We need to insert the denuncia views at the top or above ServerConfig
# and the commands inside ServerConfig.

denuncia_cmds = """
    @app_commands.command(name="denuncia_chat", description="Posta a mensagem de abertura de denúncias no canal.")
    async def denuncia_chat(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="📋 Sistema de Denúncias",
            description=(
                "Presenciou algo que viola as regras do servidor?\\n\\n"
                "Clique no botão de abrir denúncia para abrir um ticket de denúncia **privado** "
                "com a staff."
            ),
            color=discord.Color.from_rgb(220, 50, 50),
        )
        await interaction.channel.send(embed=embed, view=BotaoAbrirDenuncia())
        await interaction.response.send_message("✅ Mensagem postada.", ephemeral=True)

    @app_commands.command(name="fechar_denuncia", description="Fecha (deleta) o canal de ticket atual.")
    async def fechar_denuncia(self, interaction: discord.Interaction) -> None:
        if not interaction.channel.name.startswith("denuncia-"):
            await interaction.response.send_message("❌ Este comando só pode ser usado dentro de um canal de ticket.", ephemeral=True)
            return
        await interaction.response.send_message("🔒 Fechando o ticket...", ephemeral=True)
        await interaction.channel.delete(reason=f"Ticket fechado por {interaction.user} via comando")
"""

# Insert views before ServerConfig class
sc = sc.replace("class ServerConfig(", denuncia_views_and_commands + "\nclass ServerConfig(")

# Delete the old denuncia_chat router
import re
sc = re.sub(r'    @app_commands\.command\(name="denuncia_chat".*?async def denuncia_chat\(self, interaction: discord\.Interaction\) -> None:\n        cog = interaction\.client\.get_cog\("Denuncia"\)\n        if cog: await cog\.denuncia_chat\(interaction\)\n        else: await interaction\.response\.send_message\("Módulo indisponível\.", ephemeral=True\)', '', sc, flags=re.DOTALL)

# Insert the new commands before on_error
sc = sc.replace("    async def on_error(", denuncia_cmds + "\n    async def on_error(")

# Insert view registration in setup
sc = sc.replace("bot.tree.add_command(ServerConfig())", "bot.tree.add_command(ServerConfig())\n    bot.add_view(BotaoAbrirDenuncia())\n    bot.add_view(BotaoFecharDenuncia())")

with open("cogs/server_config.py", "w") as f:
    f.write(sc)

# Delete original cog
if os.path.exists("cogs/denuncia.py"):
    os.remove("cogs/denuncia.py")

print("Migration completed successfully.")
