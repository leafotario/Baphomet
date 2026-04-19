import discord
from discord.ext import commands
from discord import app_commands
import re

# --- CLASSE DO EDITOR (MODAL) ---
class AutoRoleEditorModal(discord.ui.Modal):
    def __init__(self, message: discord.Message, current_embed: discord.Embed):
        super().__init__(title="Editor Supreme de Autorole")
        self.message = message
        
        self.novo_titulo = discord.ui.TextInput(
            label="Título do Painel",
            default=current_embed.title,
            placeholder="Ex: Seleção de Cores",
            required=True
        )
        self.nova_desc = discord.ui.TextInput(
            label="Mensagem e Configuração (Parser)",
            style=discord.TextStyle.paragraph,
            default=current_embed.description,
            placeholder="Texto aqui... \nPara botões use: @Cargo | Emoji | Label ;",
            required=True
        )
        self.nova_cor = discord.ui.TextInput(
            label="Cor Hexadecimal",
            default=str(hex(current_embed.color.value)).replace("0x", "#"),
            placeholder="#5865F2",
            max_length=7
        )
        self.nova_imagem = discord.ui.TextInput(
            label="URL do Banner (Imagem)",
            default=current_embed.image.url if current_embed.image else "",
            placeholder="https://link-da-imagem.png",
            required=False
        )
        self.nova_thumb = discord.ui.TextInput(
            label="URL do Ícone (Thumbnail)",
            default=current_embed.thumbnail.url if current_embed.thumbnail else "",
            placeholder="https://link-do-icone.png",
            required=False
        )

        self.add_item(self.novo_titulo)
        self.add_item(self.nova_desc)
        self.add_item(self.nova_cor)
        self.add_item(self.nova_imagem)
        self.add_item(self.nova_thumb)

    async def on_submit(self, it: discord.Interaction):
        # 1. Processar a Cor
        hex_color = self.nova_cor.value.replace("#", "")
        try:
            cor = int(hex_color, 16)
        except:
            cor = self.message.embeds[0].color.value

        # 2. Re-gerar a View (Botões) baseada no texto da descrição
        view = discord.ui.View(timeout=None)
        
        # Lógica de Parser para encontrar cargos na descrição
        linhas = self.nova_desc.value.split(";")
        for linha in linhas:
            if "|" in linha:
                try:
                    partes = [p.strip() for p in linha.split("|")]
                    if len(partes) == 3:
                        mencao, emoji, label = partes
                        r_id = int(re.search(r'\d+', mencao).group())
                        view.add_item(discord.ui.Button(
                            style=discord.ButtonStyle.secondary, 
                            label=label, 
                            emoji=emoji, 
                            custom_id=f"spr_ar:{r_id}"
                        ))
                except: continue

        # 3. Montar novo Embed
        embed = discord.Embed(title=self.novo_titulo.value, description=self.nova_desc.value, color=cor)
        if self.nova_imagem.value: embed.set_image(url=self.nova_imagem.value)
        if self.nova_thumb.value: embed.set_thumbnail(url=self.nova_thumb.value)

        await self.message.edit(embed=embed, view=view if len(view.children) > 0 else None)
        await it.response.send_message("✅ Painel atualizado com sucesso via Modal!", ephemeral=True)

class SupremeAutoRole(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def create_base_embed(self, titulo, desc, cor_hex, img, thumb):
        cor = int(cor_hex.replace("#", ""), 16) if "#" in cor_hex else 0x2B2D31
        embed = discord.Embed(title=titulo, description=desc, color=cor)
        if img: embed.set_image(url=img)
        if thumb: embed.set_thumbnail(url=thumb)
        return embed

    # --- COMANDOS DE ADMIN ---
    @app_commands.command(name="autorole_unico", description="Cria painel para 1 cargo.")
    @app_commands.default_permissions(administrator=True)
    async def unico(self, it: discord.Interaction, canal: discord.TextChannel, cargo: discord.Role, 
                    emoji: str, texto_botao: str, mensagem: str, desc_cargo: str, 
                    titulo: str = "Cargo Único", cor_hex: str = "#2B2D31"):
        
        desc = f"{mensagem}\n\n-# {desc_cargo}\n\n{emoji} : {cargo.mention}"
        embed = self.create_base_embed(titulo, desc, cor_hex, None, None)
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(style=discord.ButtonStyle.primary, label=texto_botao, emoji=emoji, custom_id=f"spr_ar:{cargo.id}"))
        
        await canal.send(embed=embed, view=view)
        await it.response.send_message("✅ Painel Enviado!", ephemeral=True)

    @app_commands.command(name="autorole_deci", description="Cria painel de 6-15 cargos via Parser.")
    @app_commands.default_permissions(administrator=True)
    async def deci(self, it: discord.Interaction, canal: discord.TextChannel, configuracao: str, 
                   mensagem: str, titulo: str = "Central de Cargos", cor_hex: str = "#2B2D31"):
        
        await it.response.defer(ephemeral=True)
        view = discord.ui.View(timeout=None)
        desc = f"{mensagem}\n\n"
        
        itens = configuracao.split(";")
        for i in itens:
            try:
                p = [x.strip() for x in i.split("|")]
                if len(p) == 3:
                    r_id = int(re.search(r'\d+', p[0]).group())
                    cargo = it.guild.get_role(r_id)
                    if cargo:
                        desc += f"{p[1]} : {cargo.mention}\n"
                        view.add_item(discord.ui.Button(style=discord.ButtonStyle.secondary, label=p[2], emoji=p[1], custom_id=f"spr_ar:{cargo.id}"))
            except: continue

        embed = self.create_base_embed(titulo, desc, cor_hex, None, None)
        await canal.send(embed=embed, view=view)
        await it.followup.send("✅ Painel Ultra Enviado!", ephemeral=True)

    # --- CHAVE DE OURO: O EDITOR SUPREME ---
    @app_commands.command(name="autorole_editar", description="Abre o Modal de edição para um painel existente.")
    @app_commands.describe(link_mensagem="O link da mensagem do bot que queres editar.")
    @app_commands.default_permissions(administrator=True)
    async def editar(self, it: discord.Interaction, link_mensagem: str):
        try:
            parts = link_mensagem.split('/')
            canal = it.guild.get_channel(int(parts[-2]))
            msg = await canal.fetch_message(int(parts[-1]))
            
            if msg.author.id != self.bot.user.id or not msg.embeds:
                return await it.response.send_message("❌ Só posso editar as minhas próprias mensagens com Embeds.", ephemeral=True)
            
            await it.response.send_modal(AutoRoleEditorModal(msg, msg.embeds[0]))
        except:
            await it.response.send_message("❌ Link inválido ou mensagem não encontrada.", ephemeral=True)

    # --- O MOTOR IMORTAL ---
    @commands.Cog.listener()
    async def on_interaction(self, it: discord.Interaction):
        if it.type != discord.InteractionType.component: return
        cid = it.data.get('custom_id', '')

        if cid.startswith("spr_ar:"):
            role_id = int(cid.split(":")[1])
            cargo = it.guild.get_role(role_id)
            if not cargo: return

            try:
                if cargo in it.user.roles:
                    await it.user.remove_roles(cargo)
                    await it.response.send_message(f"Removido: **{cargo.name}**", ephemeral=True)
                else:
                    await it.user.add_roles(cargo)
                    await it.response.send_message(f"Adicionado: **{cargo.name}**", ephemeral=True)
            except:
                await it.response.send_message("❌ Erro: Verifica se o meu cargo está acima do cargo que queres atribuir.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(SupremeAutoRole(bot))