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
            default=current_embed.title[:256] if current_embed.title else "Sem Título",
            placeholder="Ex: Seleção de Cores",
            required=True
        )
        self.nova_desc = discord.ui.TextInput(
            label="Mensagem e Configuração (Parser)",
            style=discord.TextStyle.paragraph,
            default=current_embed.description[:4000] if current_embed.description else "Mensagem",
            placeholder="Texto aqui... \nPara botões use: @Cargo | Emoji | Label ;",
            required=True
        )
        
        cor_default = "#2B2D31"
        if current_embed.color:
            cor_default = str(current_embed.color)
            
        self.nova_cor = discord.ui.TextInput(
            label="Cor Hexadecimal",
            default=cor_default,
            placeholder="#5865F2",
            max_length=7,
            required=False
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
        hex_color = self.nova_cor.value.replace("#", "").strip() if self.nova_cor.value else "2B2D31"
        try:
            cor = int(hex_color, 16)
        except ValueError:
            cor = self.message.embeds[0].color.value if self.message.embeds[0].color else 0x2B2D31

        # 2. Re-gerar a View (Botões) baseada no texto da descrição
        view = discord.ui.View(timeout=None)
        
        # Lógica de Parser para encontrar cargos na descrição sem corromper o texto
        linhas = self.nova_desc.value.split("\n")
        texto_final = []
        novos_botoes = []
        
        for linha in linhas:
            if "|" in linha and ";" in linha:
                itens = [x for x in linha.split(";") if x.strip()]
                parsed_any = False
                for item in itens:
                    if "|" in item:
                        try:
                            partes = [p.strip() for p in item.split("|")]
                            if len(partes) == 3:
                                mencao, emoji, label = partes
                                r_match = re.search(r'\d+', mencao)
                                if r_match:
                                    r_id = int(r_match.group())
                                    cargo = it.guild.get_role(r_id)
                                    if cargo:
                                        novos_botoes.append((label, emoji, f"spr_ar:{r_id}", cargo.mention))
                                        parsed_any = True
                        except Exception: 
                            pass
                if not parsed_any:
                    texto_final.append(linha)
            else:
                texto_final.append(linha)

        desc = "\n".join(texto_final).strip()
        
        if novos_botoes:
            desc += "\n\n"
            for label, emoji, custom_id, mention in novos_botoes:
                desc += f"{emoji} : {mention}\n"
                view.add_item(discord.ui.Button(
                    style=discord.ButtonStyle.secondary, 
                    label=label, 
                    emoji=emoji, 
                    custom_id=custom_id
                ))
        else:
            # Mantém os botões antigos se não houve nova configuração inserida
            if self.message.components:
                for action_row in self.message.components:
                    for child in action_row.children:
                        if isinstance(child, discord.components.Button):
                            view.add_item(discord.ui.Button(
                                style=child.style,
                                label=child.label,
                                emoji=child.emoji,
                                custom_id=child.custom_id,
                                url=child.url,
                                disabled=child.disabled
                            ))

        # 3. Montar novo Embed
        embed = discord.Embed(title=self.novo_titulo.value, description=desc, color=cor)
        if self.nova_imagem.value: embed.set_image(url=self.nova_imagem.value)
        if self.nova_thumb.value: embed.set_thumbnail(url=self.nova_thumb.value)

        try:
            await self.message.edit(embed=embed, view=view if len(view.children) > 0 else None)
            await it.response.send_message("✅ Painel atualizado com sucesso via Modal!", ephemeral=True)
        except Exception as e:
            await it.response.send_message(f"❌ Erro ao atualizar o painel: `{str(e)}`", ephemeral=True)


class SupremeAutoRole(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def create_base_embed(self, titulo, desc, cor_hex, img, thumb):
        try:
            cor = int(cor_hex.replace("#", "").strip(), 16)
        except ValueError:
            cor = 0x2B2D31
            
        embed = discord.Embed(title=titulo, description=desc, color=cor)
        if img: embed.set_image(url=img)
        if thumb: embed.set_thumbnail(url=thumb)
        return embed

    # --- COMANDOS DE ADMIN ---
    @app_commands.command(name="autorole_unico", description="Cria painel para 1 cargo.")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def unico(self, it: discord.Interaction, canal: discord.TextChannel, cargo: discord.Role, 
                    emoji: str, texto_botao: str, mensagem: str, desc_cargo: str, 
                    titulo: str = "Cargo Único", cor_hex: str = "#2B2D31"):
        
        desc = f"{mensagem}\n\n-# {desc_cargo}\n\n{emoji} : {cargo.mention}"
        embed = self.create_base_embed(titulo, desc, cor_hex, None, None)
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(style=discord.ButtonStyle.primary, label=texto_botao, emoji=emoji, custom_id=f"spr_ar:{cargo.id}"))
        
        try:
            await canal.send(embed=embed, view=view)
            await it.response.send_message("✅ Painel Enviado!", ephemeral=True)
        except Exception as e:
            await it.response.send_message(f"❌ Erro ao enviar o painel: `{str(e)}`", ephemeral=True)

    @app_commands.command(name="autorole_multi", description="Cria painel para dar de 2 a 5 cargos simultaneamente.")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def multi(self, it: discord.Interaction, canal: discord.TextChannel, 
                    cargo1: discord.Role, cargo2: discord.Role,
                    emoji: str, texto_botao: str, mensagem: str,
                    cargo3: discord.Role = None, cargo4: discord.Role = None, cargo5: discord.Role = None,
                    titulo: str = "Cargos Iniciais", cor_hex: str = "#2B2D31"):
        
        cargos = [c for c in (cargo1, cargo2, cargo3, cargo4, cargo5) if c is not None]
        
        # Converte IDs para Hex para economizar espaço no custom_id (limite de 100 chars)
        hex_ids = "-".join(hex(c.id)[2:] for c in cargos)
        custom_id = f"spr_arm:{hex_ids}"
        
        mencoes = "\n".join(f"• {c.mention}" for c in cargos)
        desc = f"{mensagem}\n\n**Cargos Recebidos:**\n{mencoes}"
        
        embed = self.create_base_embed(titulo, desc, cor_hex, None, None)
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(style=discord.ButtonStyle.primary, label=texto_botao, emoji=emoji, custom_id=custom_id))
        
        try:
            await canal.send(embed=embed, view=view)
            await it.response.send_message("✅ Painel Múltiplo Enviado!", ephemeral=True)
        except Exception as e:
            await it.response.send_message(f"❌ Erro ao enviar o painel: `{str(e)}`", ephemeral=True)

    @app_commands.command(name="autorole_deci", description="Cria painel de 6-15 cargos via Parser.")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
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
                    r_match = re.search(r'\d+', p[0])
                    if not r_match: continue
                    r_id = int(r_match.group())
                    cargo = it.guild.get_role(r_id)
                    if cargo:
                        desc += f"{p[1]} : {cargo.mention}\n"
                        view.add_item(discord.ui.Button(style=discord.ButtonStyle.secondary, label=p[2], emoji=p[1], custom_id=f"spr_ar:{cargo.id}"))
            except Exception: 
                continue

        embed = self.create_base_embed(titulo, desc, cor_hex, None, None)
        
        try:
            await canal.send(embed=embed, view=view)
            await it.followup.send("✅ Painel Ultra Enviado!", ephemeral=True)
        except Exception as e:
            await it.followup.send(f"❌ Erro ao enviar o painel: `{str(e)}`", ephemeral=True)

    # --- CHAVE DE OURO: O EDITOR SUPREME ---
    @app_commands.command(name="autorole_editar", description="Abre o Modal de edição para um painel existente.")
    @app_commands.describe(link_mensagem="O link da mensagem do bot que queres editar.")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def editar(self, it: discord.Interaction, link_mensagem: str):
        try:
            match = re.search(r'channels/\d+/(\d+)/(\d+)', link_mensagem)
            if not match:
                return await it.response.send_message("❌ Link inválido. Forneça o link direto para a mensagem.", ephemeral=True)
                
            canal_id, msg_id = int(match.group(1)), int(match.group(2))
            canal = it.guild.get_channel(canal_id)
            if not canal:
                return await it.response.send_message("❌ Canal não encontrado neste servidor.", ephemeral=True)
                
            msg = await canal.fetch_message(msg_id)
            
            if msg.author.id != self.bot.user.id or not msg.embeds:
                return await it.response.send_message("❌ Só posso editar as minhas próprias mensagens que contenham Embeds.", ephemeral=True)
            
            await it.response.send_modal(AutoRoleEditorModal(msg, msg.embeds[0]))
        except discord.NotFound:
            await it.response.send_message("❌ Mensagem não encontrada. Verifica se o link está correto.", ephemeral=True)
        except discord.Forbidden:
            await it.response.send_message("❌ Não tenho permissão para ler o histórico desse canal.", ephemeral=True)
        except Exception as e:
            await it.response.send_message(f"❌ Erro ao tentar editar: `{str(e)}`", ephemeral=True)

    # --- O MOTOR IMORTAL ---
    @commands.Cog.listener()
    async def on_interaction(self, it: discord.Interaction):
        if it.type != discord.InteractionType.component: return
        
        cid = it.data.get('custom_id')
        if not cid: return

        if cid.startswith("spr_ar:"):
            try:
                role_id = int(cid.split(":")[1])
            except (IndexError, ValueError):
                return
                
            cargo = it.guild.get_role(role_id)
            if not cargo: 
                return await it.response.send_message("❌ Cargo não encontrado no servidor.", ephemeral=True)

            try:
                if cargo in it.user.roles:
                    await it.user.remove_roles(cargo)
                    await it.response.send_message(f"Removido: **{cargo.name}**", ephemeral=True)
                else:
                    await it.user.add_roles(cargo)
                    await it.response.send_message(f"Adicionado: **{cargo.name}**", ephemeral=True)
            except discord.Forbidden:
                await it.response.send_message("❌ Erro: O meu cargo precisa estar acima do cargo que queres atribuir, e eu preciso da permissão 'Gerenciar Cargos'.", ephemeral=True)
            except discord.HTTPException:
                await it.response.send_message("❌ Erro ao tentar modificar os teus cargos.", ephemeral=True)

        elif cid.startswith("spr_arm:"):
            try:
                hex_ids = cid.split(":")[1].split("-")
            except IndexError:
                return
                
            cargos = []
            for hid in hex_ids:
                try:
                    cargo = it.guild.get_role(int(hid, 16))
                    if cargo: cargos.append(cargo)
                except ValueError:
                    continue
                
            if not cargos:
                return await it.response.send_message("❌ Nenhum dos cargos foi encontrado no servidor.", ephemeral=True)

            try:
                # Se o usuário tem todos os cargos, removemos todos. Se não, damos todos.
                has_all = all(c in it.user.roles for c in cargos)
                if has_all:
                    await it.user.remove_roles(*cargos)
                    nomes = ", ".join(f"**{c.name}**" for c in cargos)
                    await it.response.send_message(f"Removidos: {nomes}", ephemeral=True)
                else:
                    await it.user.add_roles(*cargos)
                    nomes = ", ".join(f"**{c.name}**" for c in cargos)
                    await it.response.send_message(f"Adicionados: {nomes}", ephemeral=True)
            except discord.Forbidden:
                await it.response.send_message("❌ Erro de Permissão: Verifica se o meu cargo está acima dos cargos que queres atribuir.", ephemeral=True)
            except discord.HTTPException:
                await it.response.send_message("❌ Erro ao tentar modificar os teus cargos.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(SupremeAutoRole(bot))