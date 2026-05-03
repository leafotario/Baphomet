import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import datetime
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os
import aiohttp
from PIL import Image
import io

SPOTIPY_CLIENT_ID = os.getenv('SPOTIFY_ID')
SPOTIPY_CLIENT_SECRET = os.getenv('SPOTIFY_SECRET')
ARQUIVO_FILA = 'fila_albuns.json'
CANAL_ALBUM_ID = 123456789012345678  # ID do canal para postagem automática


async def cor_dominante(url: str) -> discord.Color:
    """Baixa a capa e retorna a cor mais dominante como discord.Color."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.read()
        img = Image.open(io.BytesIO(data)).convert("RGB")
        # Reduz para 1x1 — o PIL faz a média ponderada naturalmente
        img = img.resize((1, 1), Image.LANCZOS)
        r, g, b = img.getpixel((0, 0))
        return discord.Color.from_rgb(r, g, b)
    except Exception:
        return discord.Color.purple()


class AlbumDoDia(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=SPOTIPY_CLIENT_ID,
            client_secret=SPOTIPY_CLIENT_SECRET
        ))
        self.enviar_album_automatico.start()

    def cog_unload(self):
        self.enviar_album_automatico.cancel()

    # --- Funções Auxiliares ---
    def carregar_fila(self):
        if not os.path.exists(ARQUIVO_FILA):
            with open(ARQUIVO_FILA, 'w') as f:
                json.dump([], f)
        with open(ARQUIVO_FILA, 'r') as f:
            return json.load(f)

    def salvar_fila(self, fila):
        with open(ARQUIVO_FILA, 'w') as f:
            json.dump(fila, f, indent=4)

    async def despachar_album(self, destino):
        """Lógica de envio reutilizável (aceita TextChannel ou Interaction)"""
        fila = self.carregar_fila()
        
        if not fila:
            msg = "⚠️ A fila está **vazia**! Adicionem mais álbuns."
            if isinstance(destino, discord.Interaction):
                await destino.response.send_message(msg)
            else:
                await destino.send(msg)
            return
        
        album_de_hoje = fila.pop(0)
        self.salvar_fila(fila)
        
        cor = await cor_dominante(album_de_hoje['imagem']) if album_de_hoje['imagem'] else discord.Color.purple()

        minutos = album_de_hoje.get('duracao_ms', 0) // 60000
        segundos = (album_de_hoje.get('duracao_ms', 0) % 60000) // 1000
        generos = album_de_hoje.get('generos', [])
        generos_str = ', '.join(generos) if generos else None
        total_faixas = album_de_hoje.get('total_faixas', '?')

        footer_parts = [f"🎵 {total_faixas} faixas · {minutos}min {segundos:02d}s"]
        if generos_str:
            footer_parts.append(f"🎸 {generos_str}")

        embed = discord.Embed(
            title=album_de_hoje['nome'],
            description=(
                f"<@&1495234087772754011>\n\n"
                f"O álbum de hoje é **{album_de_hoje['nome']}** de **{album_de_hoje['artista']}**\n\n"
                f"E aí, já ouviu? O que achou?\n\n"
                f"<:aotd_gosto:1495227456851017758> - Eu gosto desse álbum\n"
                f"<:aotd_naogosto:1495227731791708250> - Não gosto desse álbum\n"
                f"<:aotd_nuncaouvi:1495227758224085043> - Nunca ouvi esse álbum"
            ),
            color=cor,
            url=album_de_hoje['url']
        )

        embed.set_footer(text=' | '.join(footer_parts))

        if album_de_hoje['imagem']:
            embed.set_image(url=album_de_hoje['imagem'])

        if isinstance(destino, discord.Interaction):
            await destino.response.send_message(embed=embed)
            msg = await destino.original_response()
        else:
            msg = await destino.send(embed=embed)

        await msg.add_reaction("<:aotd_gosto:1495227456851017758>")
        await msg.add_reaction("<:aotd_naogosto:1495227731791708250>")
        await msg.add_reaction("<:aotd_nuncaouvi:1495227758224085043>")

        thread = await msg.create_thread(
            name=album_de_hoje['nome'],
            auto_archive_duration=10080,
        )

        sugerido_por_id = album_de_hoje.get('sugerido_por_id')
        if sugerido_por_id:
            await thread.send(f"Sugerido por: <@{sugerido_por_id}>")
        else:
            await thread.send(f"Sugerido por: {album_de_hoje['sugerido_por']}")

    # --- Loop Automático ---
    fuso_brasil = datetime.timezone(datetime.timedelta(hours=-3))
    horario_diario = datetime.time(hour=12, minute=0, tzinfo=fuso_brasil)

    @tasks.loop(time=horario_diario)
    async def enviar_album_automatico(self):
        await self.bot.wait_until_ready()
        canal = self.bot.get_channel(CANAL_ALBUM_ID)
        if canal:
            await self.despachar_album(canal)

    # --- Slash Commands ---
    
    @app_commands.command(name="aotd_sugerir", description="Sugere um álbum para a fila do dia")
    @app_commands.describe(nome_album="Nome do álbum ou artista")
    async def sugerir(self, interaction: discord.Interaction, nome_album: str):
        # A busca pode demorar, então avisamos o Discord que estamos processando
        await interaction.response.defer() 

        resultados = self.sp.search(q=nome_album, type='album', limit=1)
        albuns_encontrados = resultados['albums']['items']

        if not albuns_encontrados:
            await interaction.followup.send(f"❌ Não encontrei '{nome_album}' no Spotify.")
            return

        album = albuns_encontrados[0]

        artist_info = self.sp.artist(album['artists'][0]['id'])
        generos_raw = artist_info.get('genres', [])
        generos = generos_raw[:4] if len(generos_raw) >= 2 else generos_raw

        faixas_data = self.sp.album_tracks(album['id'])
        duracao_ms = sum(t['duration_ms'] for t in faixas_data['items'])

        info_album = {
            'nome': album['name'],
            'artista': album['artists'][0]['name'],
            'url': album['external_urls']['spotify'],
            'imagem': album['images'][0]['url'] if album['images'] else None,
            'sugerido_por': interaction.user.name,
            'sugerido_por_id': interaction.user.id,
            'generos': generos,
            'total_faixas': album['total_tracks'],
            'duracao_ms': duracao_ms,
        }

        fila = self.carregar_fila()
        if any(a['url'] == info_album['url'] for a in fila):
            await interaction.followup.send("⚠️ Esse álbum já está na fila!")
            return

        fila.append(info_album)
        self.salvar_fila(fila)
        
        cor = await cor_dominante(info_album['imagem']) if info_album['imagem'] else discord.Color.green()

        embed = discord.Embed(
            title="🎵 Álbum Adicionado!", 
            description=f"**{info_album['nome']}** - {info_album['artista']}",
            color=cor
        )
        embed.set_thumbnail(url=info_album['imagem'])
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="aotd_fila", description="Ver status e duração da fila")
    async def ver_fila(self, interaction: discord.Interaction):
        fila = self.carregar_fila()
        qtd = len(fila)
        
        if qtd == 0:
            await interaction.response.send_message("📭 A fila está vazia.")
            return

        hoje = datetime.date.today()
        data_final = hoje + datetime.timedelta(days=max(0, qtd - 1))
        
        await interaction.response.send_message(
            f"Temos **{qtd}** álbum(ns) na fila! Garantidos até **{data_final.strftime('%d/%m/%Y')}**."
        )

    @app_commands.command(name="aotd_lista", description="Lista todos os álbuns agendados (Admin)")
    @app_commands.checks.has_permissions(administrator=True)
    async def listar_fila(self, interaction: discord.Interaction):
        fila = self.carregar_fila()
        if not fila:
            await interaction.response.send_message("A fila está vazia.")
            return
        
        linhas = [f"`{i}.` **{a['nome']}** - {a['artista']} ({a['sugerido_por']})" for i, a in enumerate(fila, 1)]
        msg = "**Fila Atual:**\n" + "\n".join(linhas)
        
        if len(msg) > 2000:
            await interaction.response.send_message("A lista é muito longa para o Discord, tente remover alguns álbuns.")
        else:
            await interaction.response.send_message(msg)

    @app_commands.command(name="aotd_remover", description="Remove um álbum pelo índice (Admin)")
    @app_commands.describe(indice="Número do álbum na !lista")
    @app_commands.checks.has_permissions(administrator=True)
    async def remover(self, interaction: discord.Interaction, indice: int):
        fila = self.carregar_fila()
        
        if indice < 1 or indice > len(fila):
            await interaction.response.send_message("❌ Índice inválido.", ephemeral=True)
            return
        
        removido = fila.pop(indice - 1)
        self.salvar_fila(fila)
        await interaction.response.send_message(f"🗑️ **{removido['nome']}** removido.")

    @app_commands.command(name="aotd_testar_album", description="Força o envio do próximo álbum agora (Admin)")
    @app_commands.checks.has_permissions(administrator=True)
    async def testar_album(self, interaction: discord.Interaction):
        await self.despachar_album(interaction)

async def setup(bot):
    await bot.add_cog(AlbumDoDia(bot))