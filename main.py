import asyncio
import logging
import os
import platform
import sys
import time

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# ==============================================================================
# 1. CORES E ESTILOS PARA TERMINAL (ANSI)
# ==============================================================================
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    BLUE = "\033[94m"

# ==============================================================================
# 2. FUNÇÕES AUXILIARES DE LOGGING (BEAUTIFUL LOGS)
# ==============================================================================
def print_separator() -> None:
    print(f"{Colors.BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Colors.RESET}")

def log_info(msg: str) -> None:
    print(f"🔹 {Colors.CYAN}[INFO]{Colors.RESET} {msg}")

def log_success(msg: str) -> None:
    print(f"✅ {Colors.GREEN}[OK]{Colors.RESET} {msg}")

def log_warn(msg: str) -> None:
    print(f"⚠️ {Colors.YELLOW}[WARN]{Colors.RESET} {msg}")

def log_error(msg: str) -> None:
    print(f"❌ {Colors.RED}[ERROR]{Colors.RESET} {msg}")

def log_cog(msg: str, success: bool = True) -> None:
    tag = f"✅ {Colors.GREEN}[COG]{Colors.RESET}" if success else f"❌ {Colors.RED}[COG]{Colors.RESET}"
    print(f"{tag} {msg}")

def log_sync(msg: str) -> None:
    print(f"⚡ {Colors.BLUE}[SYNC]{Colors.RESET} {msg}")

def log_discord(msg: str) -> None:
    print(f"🟣 {Colors.MAGENTA}[DISCORD]{Colors.RESET} {msg}")

# ==============================================================================
# 3. VALIDAÇÃO DE AMBIENTE (.ENV E TOKEN)
# ==============================================================================
def get_token() -> str:
    log_info("Carregando variáveis de ambiente...")
    load_dotenv()

    token = os.getenv("DISCORD_TOKEN") or os.getenv("BOT_TOKEN") or os.getenv("TOKEN")

    if not token:
        print_separator()
        log_error("Nenhum token encontrado! Verifique se o arquivo .env existe e possui a chave DISCORD_TOKEN.")
        sys.exit(1)

    masked_token = f"{token[:4]}****************{token[-4:]}" if len(token) > 8 else "********"
    log_success(f"Token detectado com segurança: {masked_token}")
    return token

# ==============================================================================
# 4. CLASSES DO BOT (CORE)
# ==============================================================================
class MyBot(commands.Bot):
    def __init__(self) -> None:
        self.start_time = time.time()

        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(
            command_prefix="!",
            intents=intents
        )

    async def load_all_extensions(self) -> tuple[int, int]:
        """Varre o diretório /cogs e carrega as extensões."""
        log_info("Iniciando o carregamento de extensões (Cogs)...")
        loaded, failed = 0, 0

        if not os.path.exists("./cogs"):
            log_warn("Diretório './cogs' não encontrado. Pulando o carregamento de extensões.")
            return loaded, failed

        for filename in sorted(os.listdir("./cogs")):
            if filename.startswith("__") or filename.startswith("xp_"):
                continue

            extension = None
            if filename.endswith(".py"):
                extension = f"cogs.{filename[:-3]}"
            elif os.path.isdir(f"./cogs/{filename}") and os.path.isfile(f"./cogs/{filename}/__init__.py"):
                extension = f"cogs.{filename}"

            if not extension:
                continue

            try:
                await self.load_extension(extension)
                log_cog(f"{extension} carregado com sucesso.", success=True)
                loaded += 1
            except Exception as exc:
                log_cog(f"{extension} falhou: {type(exc).__name__}: {exc}", success=False)
                failed += 1

        return loaded, failed

    async def setup_hook(self) -> None:
        """Chamado pelo discord.py antes de logar o bot."""
        loaded, failed = await self.load_all_extensions()

        print_separator()
        log_info(f"Resumo de Cogs: {loaded} carregados com sucesso | {failed} falharam.")
        print_separator()

        log_sync("Sincronizando slash commands globalmente...")
        try:
            synced = await self.tree.sync()
            log_sync(f"{len(synced)} comandos sincronizados com sucesso!")
        except Exception as exc:
            log_error(f"Falha ao sincronizar comandos: {exc}")

    async def close(self) -> None:
        """Desligamento gracioso do Bot."""
        log_warn("Iniciando processo de desligamento seguro...")

        runtime = getattr(self, "xp_runtime", None)
        if runtime is not None:
            try:
                log_info("Fechando conexão segura do repositório de XP...")
                await runtime.repository.close()
                log_success("Repositório XP encerrado com sucesso.")
            except Exception as exc:
                log_error(f"Erro ao fechar repositório XP: {exc}")

        log_discord("Bot desconectado da API.")
        await super().close()

bot = MyBot()

# ==============================================================================
# 5. EVENTOS DO DISCORD
# ==============================================================================
@bot.event
async def on_ready() -> None:
    boot_time = round(time.time() - bot.start_time, 2)
    users_count = sum(guild.member_count for guild in bot.guilds if guild.member_count)

    print_separator()
    print(f"{Colors.GREEN}{Colors.BOLD}🟢 BOT ONLINE E OPERACIONAL{Colors.RESET}")
    print(f"🤖 Usuário:           {bot.user} (ID: {bot.user.id})")
    print(f"🏠 Servidores:        {len(bot.guilds)}")
    print(f"👥 Usuários Visíveis: ~{users_count}")
    print(f"⏱️ Tempo de Boot:     {boot_time}s")
    print_separator()
    log_info("Aguardando interações dos usuários...")


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    """Trata erros de prefix commands sem poluir o console."""
    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.NotOwner):
        log_warn(f"O usuário {ctx.author} tentou usar comando restrito (!{ctx.command}).")
        return

    log_error(f"Erro na execução do comando '!{ctx.command}' por {ctx.author}: {error}")


@bot.tree.error
async def on_tree_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    """
    Handler global para erros de slash commands e grupos de comandos (app_commands.Group).
    Cobre casos que os handlers .error das Cogs não alcançam, como o grupo /antispam.
    """
    # Erros já tratados pelas Cogs chegam aqui com AppCommandError genérico —
    # evitamos duplicar respostas checando se a interação já foi respondida.
    msg: str

    if isinstance(error, app_commands.MissingPermissions):
        msg = "Você precisa ser administrador(a) para usar esse comando."
    elif isinstance(error, app_commands.BotMissingPermissions):
        missing = ", ".join(error.missing_permissions)
        msg = f"Estou sem as permissões necessárias: {missing}."
    elif isinstance(error, app_commands.CommandOnCooldown):
        msg = f"Aguarde {error.retry_after:.1f}s antes de usar esse comando novamente."
    elif isinstance(error, app_commands.NoPrivateMessage):
        msg = "Esse comando só pode ser usado dentro de um servidor."
    else:
        msg = "Ocorreu um erro inesperado ao executar esse comando."
        log_error(
            f"Erro não tratado em slash command "
            f"'{interaction.command.name if interaction.command else '?'}' "
            f"por {interaction.user}: {type(error).__name__}: {error}"
        )

    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        log_warn("Não foi possível enviar resposta de erro ao usuário (interação expirada?).")

# ==============================================================================
# 6. COMANDOS GLOBAIS (PREFIXO)
# ==============================================================================
@bot.command(name="sync")
@commands.is_owner()
async def sync_commands(ctx: commands.Context) -> None:
    """Força a sincronização dos slash commands globalmente. Uso: !sync (Restrito ao dono)"""
    log_sync(f"Sincronização manual solicitada por {ctx.author}...")
    try:
        synced = await bot.tree.sync()
        await ctx.send(f"✅ **{len(synced)}** comandos sincronizados globalmente com sucesso!")
        log_sync(f"{len(synced)} comandos sincronizados!")
    except Exception as e:
        await ctx.send(f"❌ Falha crítica ao sincronizar: {e}")
        log_error(f"Sincronização manual falhou: {e}")

# ==============================================================================
# 7. FUNÇÃO ASSÍNCRONA PRINCIPAL
# ==============================================================================

async def main() -> None:
    print_separator()
    print(f"{Colors.BOLD}{Colors.MAGENTA}🖤 BAPHOMET — DISCORD BOT{Colors.RESET}")
    print_separator()
    print(f"🚀 Inicializando o ecossistema...")
    print(f"🐍 Python:       {platform.python_version()}")
    print(f"📦 discord.py:   {discord.__version__}")
    print(f"🖥️ Sistema:      {platform.system()} {platform.release()}")
    print_separator()

    discord.utils.setup_logging(level=logging.WARNING)

    token = get_token()

    log_discord("Conectando aos servidores Gateway do Discord...")
    try:
        await bot.start(token)
    except discord.LoginFailure:
        log_error("Falha no login: O token fornecido no .env é inválido ou foi revogado.")
    except Exception as e:
        log_error(f"Erro fatal inesperado durante o tempo de execução: {e}")
    finally:
        if not bot.is_closed():
            await bot.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print_separator()
        log_warn("Sinal de interrupção recebido. O Bot foi encerrado com segurança.")
        print_separator()