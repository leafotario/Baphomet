import os

with open("cogs/server_config.py", "r") as f:
    content = f.read()

views = """
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

content = content.replace("@app_commands.default_permissions(administrator=True)\nclass ServerConfig", views + "\n@app_commands.default_permissions(administrator=True)\nclass ServerConfig")

with open("cogs/server_config.py", "w") as f:
    f.write(content)

