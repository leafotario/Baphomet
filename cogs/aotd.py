from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials


# ============================================================
#  ÁLBUM DO DIA — COG ÚNICO
#  Sem Pillow. Sem renderer. Apenas embed.
#
#  Requisitos:
#  - discord.py 2.x
#  - spotipy
#
#  Variáveis de ambiente esperadas:
#  - SPOTIFY_ID
#  - SPOTIFY_SECRET
# ============================================================

QUEUE_FILE = Path("fila_albuns.json")
CONFIG_FILE = Path("aotd_config.json")

SPOTIPY_CLIENT_ID = os.getenv("SPOTIFY_ID")
SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIFY_SECRET")

BR_TZ = dt.timezone(dt.timedelta(hours=-3), name="BRT")
DAILY_TIME = dt.time(hour=12, minute=0, tzinfo=BR_TZ)

EMOJI_GOSTO = "<:aotd_gosto:1495227456851017758>"
EMOJI_NAO_GOSTO = "<:aotd_naogosto:1495227731791708250>"
EMOJI_NUNCA_OUVI = "<:aotd_nuncaouvi:1495227758224085043>"

DEFAULT_ROLE_ID = 1495234087772754011

DEFAULT_CONFIG: dict[str, Any] = {
    "auto_enabled": True,
    "album_channel_id": None,
    "album_role_id": DEFAULT_ROLE_ID,
}


def atomic_json_save(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")

    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=4)

    tmp.replace(path)


def safe_json_load(path: Path, fallback: Any) -> Any:
    if not path.exists():
        atomic_json_save(path, fallback)
        return fallback

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    except (json.JSONDecodeError, OSError):
        backup = path.with_suffix(path.suffix + ".corrompido")

        try:
            path.replace(backup)
        except OSError:
            pass

        atomic_json_save(path, fallback)
        return fallback


class AlbumDoDia(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._spotify: Optional[spotipy.Spotify] = None
        self.enviar_album_automatico.start()

    def cog_unload(self) -> None:
        self.enviar_album_automatico.cancel()

    # ========================================================
    #  Persistência
    # ========================================================

    def carregar_config(self) -> dict[str, Any]:
        raw = safe_json_load(CONFIG_FILE, DEFAULT_CONFIG.copy())

        if not isinstance(raw, dict):
            raw = DEFAULT_CONFIG.copy()

        config = DEFAULT_CONFIG.copy()
        config.update(raw)

        return config

    def salvar_config(self, config: dict[str, Any]) -> None:
        clean = DEFAULT_CONFIG.copy()
        clean.update(config)

        atomic_json_save(CONFIG_FILE, clean)

    def carregar_fila(self) -> list[dict[str, Any]]:
        raw = safe_json_load(QUEUE_FILE, [])

        if not isinstance(raw, list):
            return []

        return [item for item in raw if isinstance(item, dict)]

    def salvar_fila(self, fila: list[dict[str, Any]]) -> None:
        atomic_json_save(QUEUE_FILE, fila)

    # ========================================================
    #  Spotify
    # ========================================================

    def get_spotify(self) -> spotipy.Spotify:
        if not SPOTIPY_CLIENT_ID or not SPOTIPY_CLIENT_SECRET:
            raise RuntimeError(
                "As variáveis SPOTIFY_ID e SPOTIFY_SECRET não estão configuradas."
            )

        if self._spotify is None:
            self._spotify = spotipy.Spotify(
                auth_manager=SpotifyClientCredentials(
                    client_id=SPOTIPY_CLIENT_ID,
                    client_secret=SPOTIPY_CLIENT_SECRET,
                ),
                requests_timeout=12,
                retries=2,
            )

        return self._spotify

    def buscar_album_spotify_sync(self, query: str) -> Optional[dict[str, Any]]:
        sp = self.get_spotify()

        resultados = sp.search(q=query, type="album", limit=1)
        albuns_encontrados = resultados.get("albums", {}).get("items", [])

        if not albuns_encontrados:
            return None

        album = albuns_encontrados[0]
        artista = album["artists"][0]

        try:
            artist_info = sp.artist(artista["id"])
            generos_raw = artist_info.get("genres", []) or []
        except Exception:
            generos_raw = []

        generos = generos_raw[:4]

        duracao_ms = 0

        try:
            tracks_page = sp.album_tracks(album["id"], limit=50)

            while tracks_page:
                duracao_ms += sum(
                    track.get("duration_ms", 0)
                    for track in tracks_page.get("items", [])
                )

                tracks_page = sp.next(tracks_page) if tracks_page.get("next") else None

        except Exception:
            duracao_ms = 0

        return {
            "nome": album.get("name", "Álbum desconhecido"),
            "artista": artista.get("name", "Artista desconhecido"),
            "url": album.get("external_urls", {}).get("spotify", ""),
            "imagem": album.get("images", [{}])[0].get("url")
            if album.get("images")
            else None,
            "generos": generos,
            "total_faixas": album.get("total_tracks", "?"),
            "duracao_ms": duracao_ms,
        }

    async def buscar_album_spotify(self, query: str) -> Optional[dict[str, Any]]:
        return await asyncio.to_thread(self.buscar_album_spotify_sync, query)

    # ========================================================
    #  Utilidades
    # ========================================================

    @staticmethod
    def now_br() -> dt.datetime:
        return dt.datetime.now(BR_TZ)

    @staticmethod
    def next_noon() -> dt.datetime:
        now = AlbumDoDia.now_br()
        noon_today = now.replace(hour=12, minute=0, second=0, microsecond=0)

        if now < noon_today:
            return noon_today

        return noon_today + dt.timedelta(days=1)

    @classmethod
    def queue_until_date(cls, queue_size: int) -> Optional[dt.date]:
        if queue_size <= 0:
            return None

        return cls.next_noon().date() + dt.timedelta(days=queue_size - 1)

    @staticmethod
    def fmt_bool(value: bool) -> str:
        return "Ativado" if value else "Desativado"

    @staticmethod
    def channel_mention(channel_id: Optional[int]) -> str:
        return f"<#{channel_id}>" if channel_id else "Não configurado"

    @staticmethod
    def build_role_ping(role_id: Optional[int]) -> Optional[str]:
        if not role_id:
            return None

        return f"<@&{role_id}>"

    def role_display(self, guild: Optional[discord.Guild], role_id: Optional[int]) -> str:
        if not role_id:
            return "Não configurado"

        role = guild.get_role(int(role_id)) if guild else None

        return role.mention if role else f"<@&{role_id}>"

    async def resolve_album_channel(
        self,
        channel_id: Optional[int],
    ) -> Optional[discord.abc.Messageable]:
        if not channel_id:
            return None

        channel = self.bot.get_channel(int(channel_id))

        if channel is not None and hasattr(channel, "send"):
            return channel

        try:
            fetched = await self.bot.fetch_channel(int(channel_id))

            if hasattr(fetched, "send"):
                return fetched

        except (discord.HTTPException, discord.Forbidden, discord.NotFound):
            return None

        return None

    async def send_destination(
        self,
        destino: discord.Interaction | discord.abc.Messageable,
        *,
        content: Optional[str] = None,
        embed: Optional[discord.Embed] = None,
        ephemeral: bool = False,
        allowed_mentions: Optional[discord.AllowedMentions] = None,
    ) -> Optional[discord.Message]:
        if isinstance(destino, discord.Interaction):
            if destino.response.is_done():
                return await destino.followup.send(
                    content=content,
                    embed=embed,
                    ephemeral=ephemeral,
                    allowed_mentions=allowed_mentions,
                    wait=True,
                )

            await destino.response.send_message(
                content=content,
                embed=embed,
                ephemeral=ephemeral,
                allowed_mentions=allowed_mentions,
            )

            return await destino.original_response()

        return await destino.send(
            content=content,
            embed=embed,
            allowed_mentions=allowed_mentions,
        )

    # ========================================================
    #  Embed do Álbum
    # ========================================================

    def criar_embed_album(self, album: dict[str, Any]) -> discord.Embed:
        nome = album.get("nome", "Álbum desconhecido")
        artista = album.get("artista", "Artista desconhecido")
        url = album.get("url") or None
        imagem = album.get("imagem")

        minutos = int(album.get("duracao_ms") or 0) // 60000
        segundos = (int(album.get("duracao_ms") or 0) % 60000) // 1000

        generos = album.get("generos") or []
        total_faixas = album.get("total_faixas", "?")

        footer_parts = [
            f"🎵 {total_faixas} faixas · {minutos}min {segundos:02d}s"
        ]

        if generos:
            footer_parts.append("🎸 " + ", ".join(str(g) for g in generos[:4]))

        embed = discord.Embed(
            title=f"🎧 {nome}",
            description=(
                f"O álbum de hoje é **{nome}** de **{artista}**.\n\n"
                f"E aí, já ouviu? O que achou?\n\n"
                f"{EMOJI_GOSTO} — Eu gosto desse álbum\n"
                f"{EMOJI_NAO_GOSTO} — Não gosto desse álbum\n"
                f"{EMOJI_NUNCA_OUVI} — Nunca ouvi esse álbum"
            ),
            color=discord.Color.blurple(),
            url=url,
        )

        if imagem:
            embed.set_image(url=imagem)

        embed.set_footer(text=" | ".join(footer_parts))

        return embed

    # ========================================================
    #  Envio do Álbum
    # ========================================================

    async def despachar_album(
        self,
        destino: discord.Interaction | discord.abc.Messageable,
    ) -> None:
        fila = self.carregar_fila()

        if not fila:
            await self.send_destination(
                destino,
                content="⚠️ A fila está vazia. Adicionem mais álbuns com `/aotd_sugerir`.",
                ephemeral=isinstance(destino, discord.Interaction),
            )
            return

        album_de_hoje = fila.pop(0)
        self.salvar_fila(fila)

        config = self.carregar_config()
        role_ping = self.build_role_ping(config.get("album_role_id"))

        embed = self.criar_embed_album(album_de_hoje)

        msg = await self.send_destination(
            destino,
            content=role_ping,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(roles=True),
        )

        if msg is None:
            return

        for emoji in (EMOJI_GOSTO, EMOJI_NAO_GOSTO, EMOJI_NUNCA_OUVI):
            try:
                await msg.add_reaction(emoji)
            except (discord.HTTPException, discord.Forbidden, TypeError):
                continue

        try:
            thread = await msg.create_thread(
                name=str(album_de_hoje.get("nome") or "Álbum do Dia")[:100],
                auto_archive_duration=10080,
            )

            sugerido_por_id = album_de_hoje.get("sugerido_por_id")

            if sugerido_por_id:
                await thread.send(f"✨ Sugerido por: <@{sugerido_por_id}>")
            else:
                await thread.send(
                    f"✨ Sugerido por: {album_de_hoje.get('sugerido_por', 'Comunidade')}"
                )

        except (discord.HTTPException, discord.Forbidden):
            pass

    # ========================================================
    #  Loop Automático
    # ========================================================

    @tasks.loop(time=DAILY_TIME)
    async def enviar_album_automatico(self) -> None:
        await self.bot.wait_until_ready()

        config = self.carregar_config()

        if not config.get("auto_enabled", True):
            return

        channel = await self.resolve_album_channel(config.get("album_channel_id"))

        if channel is None:
            return

        await self.despachar_album(channel)

    @enviar_album_automatico.before_loop
    async def before_enviar_album_automatico(self) -> None:
        await self.bot.wait_until_ready()

    # ========================================================
    #  Slash Commands — Usuários
    # ========================================================

    @app_commands.command(
        name="aotd_sugerir",
        description="Sugere um álbum para a fila do Álbum do Dia.",
    )
    @app_commands.describe(
        nome_album="Nome do álbum ou artista para buscar no Spotify.",
    )
    async def sugerir(
        self,
        interaction: discord.Interaction,
        nome_album: str,
    ) -> None:
        await interaction.response.defer(thinking=True)

        try:
            album_info = await self.buscar_album_spotify(nome_album)

        except RuntimeError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return

        except Exception:
            await interaction.followup.send(
                "❌ Não consegui consultar o Spotify agora. Tenta de novo daqui a pouco.",
                ephemeral=True,
            )
            return

        if not album_info:
            await interaction.followup.send(
                f"❌ Não encontrei **{nome_album}** no Spotify.",
                ephemeral=True,
            )
            return

        album_info["sugerido_por"] = interaction.user.name
        album_info["sugerido_por_id"] = interaction.user.id

        fila = self.carregar_fila()
        album_url = album_info.get("url")

        if album_url and any(item.get("url") == album_url for item in fila):
            await interaction.followup.send(
                "⚠️ Esse álbum já está na fila.",
                ephemeral=True,
            )
            return

        fila.append(album_info)
        self.salvar_fila(fila)

        embed = discord.Embed(
            title="🎵 Álbum adicionado à fila!",
            description=(
                f"**{album_info['nome']}** — {album_info['artista']}\n\n"
                f"Use `/aotd_fila` para ver o tamanho da fila."
            ),
            color=discord.Color.green(),
            url=album_info.get("url") or None,
        )

        if album_info.get("imagem"):
            embed.set_thumbnail(url=album_info["imagem"])

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="aotd_fila",
        description="Mostra quantos álbuns existem na fila.",
    )
    async def ver_fila(self, interaction: discord.Interaction) -> None:
        fila = self.carregar_fila()
        qtd = len(fila)

        if qtd == 0:
            await interaction.response.send_message("📭 A fila está vazia.")
            return

        data_final = self.queue_until_date(qtd)
        proximo = fila[0]

        await interaction.response.send_message(
            f"📚 Temos **{qtd}** álbum(ns) na fila.\n"
            f"🎧 Próximo: **{proximo.get('nome', 'Sem nome')}** — "
            f"{proximo.get('artista', 'Sem artista')}\n"
            f"🗓️ Com o envio diário ativo, a fila cobre até "
            f"**{data_final.strftime('%d/%m/%Y')}**."
        )

    # ========================================================
    #  Slash Commands — Admin
    # ========================================================

    @app_commands.command(
        name="aotd_config_canal",
        description="Define o canal onde o Álbum do Dia será enviado às 12:00.",
    )
    @app_commands.describe(
        canal="Canal de texto do Álbum do Dia. Deixe vazio para remover o canal configurado.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def configurar_canal(
        self,
        interaction: discord.Interaction,
        canal: Optional[discord.TextChannel] = None,
    ) -> None:
        config = self.carregar_config()
        config["album_channel_id"] = canal.id if canal else None
        self.salvar_config(config)

        if canal:
            await interaction.response.send_message(
                f"✅ Canal do Álbum do Dia configurado para {canal.mention}.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "✅ Canal do Álbum do Dia removido da configuração.",
                ephemeral=True,
            )

    @app_commands.command(
        name="aotd_config_cargo",
        description="Define o cargo marcado no post do Álbum do Dia.",
    )
    @app_commands.describe(
        cargo="Cargo que será marcado. Deixe vazio para desativar o ping de cargo.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def configurar_cargo(
        self,
        interaction: discord.Interaction,
        cargo: Optional[discord.Role] = None,
    ) -> None:
        config = self.carregar_config()
        config["album_role_id"] = cargo.id if cargo else None
        self.salvar_config(config)

        if cargo:
            await interaction.response.send_message(
                f"✅ Cargo do Álbum do Dia configurado para {cargo.mention}.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "✅ Ping de cargo do Álbum do Dia desativado.",
                ephemeral=True,
            )

    @app_commands.command(
        name="aotd_ativar",
        description="Ativa o envio automático diário do Álbum do Dia às 12:00.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def ativar_auto(self, interaction: discord.Interaction) -> None:
        config = self.carregar_config()
        config["auto_enabled"] = True
        self.salvar_config(config)

        aviso = ""

        if not config.get("album_channel_id"):
            aviso = (
                "\n⚠️ Configure um canal com `/aotd_config_canal`, "
                "senão o envio automático não terá onde postar."
            )

        await interaction.response.send_message(
            f"✅ Envio automático ativado para todos os dias às **12:00 BRT**.\n"
            f"📌 Canal atual: {self.channel_mention(config.get('album_channel_id'))}"
            f"{aviso}",
            ephemeral=True,
        )

    @app_commands.command(
        name="aotd_desativar",
        description="Desativa o envio automático diário do Álbum do Dia.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def desativar_auto(self, interaction: discord.Interaction) -> None:
        config = self.carregar_config()
        config["auto_enabled"] = False
        self.salvar_config(config)

        await interaction.response.send_message(
            "🛑 Envio automático do Álbum do Dia desativado.",
            ephemeral=True,
        )

    @app_commands.command(
        name="aotd_status",
        description="Mostra a configuração atual do Álbum do Dia.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def status(self, interaction: discord.Interaction) -> None:
        config = self.carregar_config()
        fila = self.carregar_fila()

        qtd = len(fila)
        data_final = self.queue_until_date(qtd)
        proximo = fila[0] if fila else None

        spotify_ok = bool(SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET)

        embed = discord.Embed(
            title="📊 Status do Álbum do Dia",
            color=discord.Color.blurple(),
            timestamp=self.now_br(),
        )

        embed.add_field(
            name="Automático",
            value=self.fmt_bool(bool(config.get("auto_enabled", True))),
            inline=True,
        )

        embed.add_field(
            name="Horário",
            value="Todos os dias às **12:00 BRT**",
            inline=True,
        )

        embed.add_field(
            name="Spotify",
            value="Configurado" if spotify_ok else "Sem credenciais",
            inline=True,
        )

        embed.add_field(
            name="Canal",
            value=self.channel_mention(config.get("album_channel_id")),
            inline=True,
        )

        embed.add_field(
            name="Cargo marcado",
            value=self.role_display(interaction.guild, config.get("album_role_id")),
            inline=True,
        )

        embed.add_field(
            name="Fila",
            value=f"{qtd} álbum(ns)",
            inline=True,
        )

        if proximo:
            embed.add_field(
                name="Próximo álbum",
                value=(
                    f"**{proximo.get('nome', 'Sem nome')}** — "
                    f"{proximo.get('artista', 'Sem artista')}"
                ),
                inline=False,
            )
        else:
            embed.add_field(
                name="Próximo álbum",
                value="Nenhum álbum na fila.",
                inline=False,
            )

        if data_final:
            embed.add_field(
                name="Cobertura estimada",
                value=f"Até **{data_final.strftime('%d/%m/%Y')}**",
                inline=False,
            )

        if not config.get("album_channel_id"):
            embed.set_footer(
                text="Dica: use /aotd_config_canal para definir onde o post automático vai cair."
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="aotd_lista",
        description="Lista os álbuns agendados na fila. Admin.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def listar_fila(self, interaction: discord.Interaction) -> None:
        fila = self.carregar_fila()

        if not fila:
            await interaction.response.send_message(
                "📭 A fila está vazia.",
                ephemeral=True,
            )
            return

        linhas = []

        for i, album in enumerate(fila, 1):
            linhas.append(
                f"`{i:02d}.` **{album.get('nome', 'Sem nome')}** — "
                f"{album.get('artista', 'Sem artista')} "
                f"({album.get('sugerido_por', 'Comunidade')})"
            )

        chunks: list[str] = []
        atual = "**Fila atual do Álbum do Dia:**\n"

        for linha in linhas:
            if len(atual) + len(linha) + 1 > 1900:
                chunks.append(atual)
                atual = ""

            atual += linha + "\n"

        if atual.strip():
            chunks.append(atual)

        await interaction.response.send_message(chunks[0], ephemeral=True)

        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True)

    @app_commands.command(
        name="aotd_remover",
        description="Remove um álbum da fila pelo índice. Admin.",
    )
    @app_commands.describe(
        indice="Número do álbum na /aotd_lista.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def remover(
        self,
        interaction: discord.Interaction,
        indice: int,
    ) -> None:
        fila = self.carregar_fila()

        if indice < 1 or indice > len(fila):
            await interaction.response.send_message(
                "❌ Índice inválido.",
                ephemeral=True,
            )
            return

        removido = fila.pop(indice - 1)
        self.salvar_fila(fila)

        await interaction.response.send_message(
            f"🗑️ **{removido.get('nome', 'Álbum')}** removido da fila.",
            ephemeral=True,
        )

    @app_commands.command(
        name="aotd_testar_album",
        description="Força o envio do próximo álbum agora. Admin.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def testar_album(self, interaction: discord.Interaction) -> None:
        await self.despachar_album(interaction)

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            msg = "🚫 Você precisa ser administrador para usar esse comando."
        else:
            msg = "❌ Ocorreu um erro ao executar esse comando."

        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AlbumDoDia(bot))