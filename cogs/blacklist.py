import discord
from discord.ext import commands
import sqlite3

class BlacklistCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_file = 'blacklist.db'
        self.blacklisted_channels = set()
        self._init_db()

    def _init_db(self):
        """Inicializa o banco de dados SQLite e carrega os canais para a memória (cache)."""
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS blacklist (
                    channel_id INTEGER PRIMARY KEY
                )
            ''')
            cursor.execute('SELECT channel_id FROM blacklist')
            rows = cursor.fetchall()
            # Carrega tudo em um set na memória para não consultar o banco em cada comando
            self.blacklisted_channels = {row[0] for row in rows}
            conn.commit()

    def _adicionar_ao_banco(self, channel_id):
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT OR IGNORE INTO blacklist (channel_id) VALUES (?)', (channel_id,))
            conn.commit()

    def _remover_do_banco(self, channel_id):
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM blacklist WHERE channel_id = ?', (channel_id,))
            conn.commit()

    async def bot_check(self, ctx):
        """Verificação global: Impede que comandos sejam executados em canais bloqueados."""
        if ctx.channel.id in self.blacklisted_channels:
            # Retorna False para impedir a execução do comando
            return False
        return True

    @commands.command(name="bloquear", help="Bloqueia o uso de comandos em um canal.")
    @commands.has_permissions(administrator=True)
    async def bloquear_canal(self, ctx, canal: discord.TextChannel = None):
        canal = canal or ctx.channel
        
        if canal.id in self.blacklisted_channels:
            await ctx.send(f"O canal {canal.mention} já está bloqueado para comandos.")
        else:
            self.blacklisted_channels.add(canal.id)
            self._adicionar_ao_banco(canal.id)
            await ctx.send(f"✅ O canal {canal.mention} foi adicionado à lista negra. Comandos não funcionarão mais lá.")

    @commands.command(name="desbloquear", help="Desbloqueia o uso de comandos em um canal.")
    @commands.has_permissions(administrator=True)
    async def desbloquear_canal(self, ctx, canal: discord.TextChannel = None):
        canal = canal or ctx.channel
        
        if canal.id in self.blacklisted_channels:
            self.blacklisted_channels.remove(canal.id)
            self._remover_do_banco(canal.id)
            await ctx.send(f"✅ O canal {canal.mention} foi removido da lista negra. Comandos liberados.")
        else:
            await ctx.send(f"O canal {canal.mention} não está na lista negra.")

    @commands.command(name="status_canais", aliases=["listanegra"], help="Mostra quais canais estão bloqueados.")
    @commands.has_permissions(administrator=True)
    async def status_canais(self, ctx):
        if not self.blacklisted_channels:
            await ctx.send("Nenhum canal está na lista negra de comandos no momento.")
            return

        canais_mencionados = []
        for channel_id in self.blacklisted_channels:
            canal_obj = self.bot.get_channel(channel_id)
            if canal_obj:
                canais_mencionados.append(canal_obj.mention)
            else:
                canais_mencionados.append(f"Canal Deletado/Desconhecido (ID: {channel_id})")

        embed = discord.Embed(
            title="🚫 Canais na Lista Negra",
            description="Os comandos do bot estão desativados nos seguintes canais:\n\n" + "\n".join(canais_mencionados),
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)

    @bloquear_canal.error
    @desbloquear_canal.error
    @status_canais.error
    async def admin_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Você não tem permissão de Administrador para usar este comando.")

async def setup(bot):
    await bot.add_cog(BlacklistCog(bot))