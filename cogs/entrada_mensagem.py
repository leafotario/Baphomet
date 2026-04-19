import discord
from discord.ext import commands
from discord import app_commands
import json
import os

class SupremeAutoRole(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config_file = "config_entrada.json"
        self.config = self.load_config()

    # --- MOTOR DE PERSISTÊNCIA (CHAVE DE OURO) ---
    def load_config(self):
        if os.path.exists(self.config_file):
            with open(self.config_file, "r") as f:
                return json.load(f)
        return {"welcome_channel": None, "rules_id": None, "intro_id": None, "roles_id": None}

    def save_config(self):
        with open(self.config_file, "w") as f:
            json.dump(self.config, f)

    # --- COMANDO DE CONFIGURAÇÃO SUPREME ---
    @app_commands.command(name="entrada_chat", description="Configura o sistema de boas-vindas com canais clicáveis.")
    @app_commands.describe(
        canal_boas_vindas="Onde a mensagem será enviada",
        canal_regras="Canal de Regras (para o link clicar e ir)",
        canal_apresentacao="Canal de Apresentações",
        canal_cargos="Canal de Auto-Role/Cargos"
    )
    @app_commands.default_permissions(administrator=True)
    async def entrada_chat(self, it: discord.Interaction, 
                           canal_boas_vindas: discord.TextChannel,
                           canal_regras: discord.TextChannel = None,
                           canal_apresentacao: discord.TextChannel = None,
                           canal_cargos: discord.TextChannel = None):
        
        self.config["welcome_channel"] = canal_boas_vindas.id
        if canal_regras: self.config["rules_id"] = canal_regras.id
        if canal_apresentacao: self.config["intro_id"] = canal_apresentacao.id
        if canal_cargos: self.config["roles_id"] = canal_cargos.id
        
        self.save_config()
        
        await it.response.send_message(f"✅ **Sistema de Boas-Vindas Configurado!**\nDestino: {canal_boas_vindas.mention}", ephemeral=True)

    # --- EVENTO DE ENTRADA DINÂMICO ---
    @commands.Cog.listener()
    async def on_member_join(self, member):
        channel_id = self.config.get("welcome_channel")
        if not channel_id: return
        
        channel = self.bot.get_channel(channel_id)
        if not channel: return

        # Pegando as menções ou usando texto padrão se não configurado
        def get_mention(key, default):
            c_id = self.config.get(key)
            return f"<#{c_id}>" if c_id else default

        regras = get_mention("rules_id", "#📖・regras")
        apresentacoes = get_mention("intro_id", "#👋・apresentações")
        cargos = get_mention("roles_id", "#🎪・cargos")

        # Texto adaptado e potente
        boas_vindas = (
            f":wave: {member.mention} Seja bem vindo(a) a **Nisoque**!\n"
            f"* Leia as {regras} e conte um pouco mais de você em {apresentacoes}!\n"
            f"* Não esqueça de pegar seus cargos em {cargos}!\n\n"
            f"> Fique a vontade para entrar nas calls e participar de nossas atividades! :sparkles:"
        )
        
        await channel.send(boas_vindas)