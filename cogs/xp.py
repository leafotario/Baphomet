import discord
from discord.ext import commands
from discord import app_commands
import json
import pathlib
import random
import time
import aiohttp
import io
from PIL import Image, ImageDraw, ImageFont, ImageOps

# ─────────────────────────────────────────────
#  CAMINHOS E CONSTANTES
# ─────────────────────────────────────────────
DATA_DIR    = pathlib.Path("data")
XP_FILE     = DATA_DIR / "xp_data.json"
CONFIG_FILE = DATA_DIR / "xp_config.json"
DATA_DIR.mkdir(exist_ok=True)

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG  = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

DEFAULT_CONFIG = {
    "xp_min":          15,
    "xp_max":          25,
    "cooldown":        60,        # segundos entre ganhos de XP
    "blacklist":       [],        # IDs de canais ignorados
    "level_roles":     {},        # {"nivel": role_id}
    "levelup_channel": None,      # ID do canal de anúncios (None = mesmo canal)
}


# ─────────────────────────────────────────────
#  MATEMÁTICA DE XP
# ─────────────────────────────────────────────
def xp_to_next_level(level: int) -> int:
    """XP necessário para sair do nível N e ir para N+1."""
    return 5 * (level ** 2) + 50 * level + 100


def compute_level(total_xp: int) -> tuple[int, int, int]:
    """Retorna (nivel_atual, xp_no_nivel_atual, xp_necessario_pro_proximo)."""
    level = 0
    accumulated = 0
    while True:
        needed = xp_to_next_level(level)
        if accumulated + needed > total_xp:
            return level, total_xp - accumulated, needed
        accumulated += needed
        level += 1


# ─────────────────────────────────────────────
#  PERSISTÊNCIA
# ─────────────────────────────────────────────
def load_xp() -> dict:
    return json.loads(XP_FILE.read_text()) if XP_FILE.exists() else {}


def save_xp(data: dict):
    XP_FILE.write_text(json.dumps(data, indent=2))


def load_config() -> dict:
    return json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}


def save_config(data: dict):
    CONFIG_FILE.write_text(json.dumps(data, indent=2))


def get_guild_config(config: dict, guild_id: str) -> dict:
    cfg = config.setdefault(guild_id, {})
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


# ─────────────────────────────────────────────
#  GERAÇÃO DA RANK CARD
# ─────────────────────────────────────────────
CARD_W, CARD_H = 900, 280


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default(size=size)


def _rr(draw: ImageDraw.ImageDraw, xy, r: int, fill=None, outline=None, width=0):
    draw.rounded_rectangle(xy, radius=r, fill=fill, outline=outline, width=width)


def _circle_avatar(img: Image.Image, size: int) -> Image.Image:
    img = ImageOps.fit(img.convert("RGBA"), (size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    img.putalpha(mask)
    return img


async def _fetch_avatar(url: str) -> Image.Image | None:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as r:
                return Image.open(io.BytesIO(await r.read())).convert("RGBA")
    except Exception:
        return None


async def generate_rank_card(
    member: discord.Member,
    total_xp: int,
    rank: int,
    level: int,
    xp_current: int,
    xp_needed: int,
) -> discord.File:
    # ── canvas ──
    img  = Image.new("RGBA", (CARD_W, CARD_H), (15, 15, 25, 255))
    draw = ImageDraw.Draw(img)

    # painel interno
    _rr(draw, (20, 18, CARD_W - 20, CARD_H - 18), 20, (25, 25, 40, 255))

    # barra de acento lateral
    _rr(draw, (0, 0, 10, CARD_H), 5, (120, 60, 240, 255))

    # ── avatar ──
    av_r  = 88
    av_cx, av_cy = 125, CARD_H // 2
    av_url = str(member.display_avatar.replace(size=256, format="png").url)
    av_img = await _fetch_avatar(av_url)

    if av_img:
        av_circle = _circle_avatar(av_img, av_r * 2)
        # borda roxa
        border = Image.new("RGBA", (av_r * 2 + 10, av_r * 2 + 10), (0, 0, 0, 0))
        ImageDraw.Draw(border).ellipse((0, 0, av_r * 2 + 9, av_r * 2 + 9), fill=(120, 60, 240, 255))
        img.paste(border, (av_cx - av_r - 5, av_cy - av_r - 5), border)
        img.paste(av_circle, (av_cx - av_r, av_cy - av_r), av_circle)
    else:
        draw.ellipse(
            (av_cx - av_r - 5, av_cy - av_r - 5, av_cx + av_r + 5, av_cy + av_r + 5),
            fill=(120, 60, 240),
        )
        draw.ellipse(
            (av_cx - av_r, av_cy - av_r, av_cx + av_r, av_cy + av_r),
            fill=(50, 50, 70),
        )

    # ── fontes ──
    f_rank    = _font(FONT_BOLD, 38)
    f_name    = _font(FONT_BOLD, 36)
    f_sub     = _font(FONT_REG,  20)
    f_nivel_l = _font(FONT_REG,  20)
    f_nivel_n = _font(FONT_BOLD, 30)
    f_xp      = _font(FONT_REG,  18)

    tx = 232  # início da área de texto

    # rank + username na mesma linha
    rank_txt = f"#{rank}"
    draw.text((tx, 36), rank_txt, font=f_rank, fill=(120, 60, 240))
    rank_w = draw.textlength(rank_txt, font=f_rank)
    name_txt = member.display_name[:22]
    draw.text((tx + rank_w + 12, 40), name_txt, font=f_name, fill=(230, 230, 245))

    # "no servidor"
    draw.text((tx, 90), "no servidor", font=f_sub, fill=(110, 110, 145))

    # NÍVEL X
    draw.text((tx, 128), "NÍVEL", font=f_nivel_l, fill=(120, 60, 240))
    draw.text((tx + 72, 120), str(level), font=f_nivel_n, fill=(230, 230, 245))

    # ── barra de XP ──
    bar_x, bar_y = tx, 186
    bar_w, bar_h = 630, 36

    _rr(draw, (bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), 18, (35, 35, 55))

    ratio   = min(xp_current / xp_needed, 1.0) if xp_needed > 0 else 0
    fill_px = int(bar_w * ratio)
    if fill_px > 36:
        _rr(draw, (bar_x, bar_y, bar_x + fill_px, bar_y + bar_h), 18, (120, 60, 240))

    xp_label = f"{xp_current:,} / {xp_needed:,} XP"
    lw = draw.textlength(xp_label, font=f_xp)
    draw.text((bar_x + bar_w // 2 - lw // 2, bar_y + 9), xp_label, font=f_xp, fill=(200, 200, 220))

    # XP total abaixo da barra
    draw.text(
        (bar_x + bar_w - draw.textlength(f"Total: {total_xp:,} XP", font=f_xp), bar_y + bar_h + 8),
        f"Total: {total_xp:,} XP",
        font=f_xp,
        fill=(90, 90, 115),
    )

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return discord.File(buf, filename="rank.png")


# ─────────────────────────────────────────────
#  VIEW — LEADERBOARD "MOSTRAR TODOS"
# ─────────────────────────────────────────────
class MostrarTodosView(discord.ui.View):
    def __init__(self, rows: list[tuple[str, int, int]]):
        super().__init__(timeout=120)
        self.rows = rows  # [(display_name, level, total_xp)]

    @discord.ui.button(label="Mostrar todos", style=discord.ButtonStyle.secondary, emoji="📋")
    async def mostrar_todos(self, interaction: discord.Interaction, button: discord.ui.Button):
        linhas = ["**🏆 Ranking completo**\n"]
        for i, (nome, level, total) in enumerate(self.rows, 1):
            linhas.append(f"`#{i:>3}` **{nome}** — Nível {level} · {total:,} XP")
        texto = "\n".join(linhas)
        if len(texto) > 1900:
            texto = texto[:1900] + "\n*(lista truncada)*"
        button.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(texto, ephemeral=True)


# ─────────────────────────────────────────────
#  COG
# ─────────────────────────────────────────────
class XP(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot      = bot
        self.xp_data  = load_xp()
        self.xp_cfg   = load_config()
        self._cd: dict[str, float] = {}  # cooldown em memória

    # ── helpers internos ──────────────────────

    def _gcfg(self, guild_id: int) -> dict:
        return get_guild_config(self.xp_cfg, str(guild_id))

    def _get_xp(self, guild_id: int, user_id: int) -> int:
        return self.xp_data.get(str(guild_id), {}).get(str(user_id), {}).get("xp", 0)

    def _set_xp(self, guild_id: int, user_id: int, xp: int):
        gid, uid = str(guild_id), str(user_id)
        self.xp_data.setdefault(gid, {}).setdefault(uid, {})["xp"] = max(0, xp)
        save_xp(self.xp_data)

    def _leaderboard(self, guild: discord.Guild) -> list[tuple[discord.Member, int]]:
        raw = self.xp_data.get(str(guild.id), {})
        result = []
        for uid, data in raw.items():
            m = guild.get_member(int(uid))
            if m and not m.bot:
                result.append((m, data.get("xp", 0)))
        result.sort(key=lambda x: x[1], reverse=True)
        return result

    def _rank_of(self, guild: discord.Guild, user_id: int) -> int:
        for i, (m, _) in enumerate(self._leaderboard(guild), 1):
            if m.id == user_id:
                return i
        return 0

    async def _grant_roles(self, member: discord.Member, level: int, cfg: dict):
        for lvl_str, role_id in cfg.get("level_roles", {}).items():
            if level >= int(lvl_str):
                role = member.guild.get_role(int(role_id))
                if role and role not in member.roles:
                    try:
                        await member.add_roles(role, reason=f"XP: nível {lvl_str} atingido")
                    except discord.Forbidden:
                        pass

    # ── listener de mensagens ─────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        cfg = self._gcfg(message.guild.id)

        if message.channel.id in cfg.get("blacklist", []):
            return

        key = f"{message.guild.id}:{message.author.id}"
        now = time.time()
        if now - self._cd.get(key, 0) < cfg.get("cooldown", 60):
            return
        self._cd[key] = now

        gained  = random.randint(cfg.get("xp_min", 15), cfg.get("xp_max", 25))
        old_xp  = self._get_xp(message.guild.id, message.author.id)
        new_xp  = old_xp + gained
        self._set_xp(message.guild.id, message.author.id, new_xp)

        old_lvl, _, _ = compute_level(old_xp)
        new_lvl, _, _ = compute_level(new_xp)

        if new_lvl > old_lvl:
            await self._grant_roles(message.author, new_lvl, cfg)
            ch_id = cfg.get("levelup_channel")
            ch    = message.guild.get_channel(ch_id) if ch_id else message.channel
            if ch:
                embed = discord.Embed(
                    description=f"🎉 {message.author.mention} subiu para o **nível {new_lvl}**!",
                    color=discord.Color.from_rgb(120, 60, 240),
                )
                try:
                    await ch.send(embed=embed)
                except discord.Forbidden:
                    pass

    # ── /rank ─────────────────────────────────

    @app_commands.command(name="rank", description="Mostra a sua carta de XP.")
    @app_commands.describe(membro="Membro para consultar (padrão: você mesmo)")
    async def rank(self, interaction: discord.Interaction, membro: discord.Member = None):
        await interaction.response.defer()
        target = membro or interaction.user
        if target.bot:
            await interaction.followup.send("❌ Bots não têm XP.", ephemeral=True)
            return

        total   = self._get_xp(interaction.guild.id, target.id)
        lvl, cur, needed = compute_level(total)
        rank    = self._rank_of(interaction.guild, target.id)

        card = await generate_rank_card(target, total, rank, lvl, cur, needed)
        await interaction.followup.send(file=card)

    # ── /leaderboard ──────────────────────────

    @app_commands.command(name="leaderboard", description="Top 5 de XP do servidor.")
    async def leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer()
        lb = self._leaderboard(interaction.guild)

        if not lb:
            await interaction.followup.send("Ninguém tem XP ainda!", ephemeral=True)
            return

        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        embed  = discord.Embed(
            title="🏆 Top 5 — Ranking de XP",
            color=discord.Color.from_rgb(120, 60, 240),
        )
        for i, (member, total) in enumerate(lb[:5]):
            lvl, cur, needed = compute_level(total)
            pct = int((cur / needed) * 100) if needed else 0
            embed.add_field(
                name=f"{medals[i]}  {member.display_name}",
                value=f"Nível **{lvl}** · {total:,} XP total · `{pct}%` para o próx.",
                inline=False,
            )

        rows = [(m.display_name, compute_level(x)[0], x) for m, x in lb]
        await interaction.followup.send(embed=embed, view=MostrarTodosView(rows))

    # ── /xp_reset_user ────────────────────────

    @app_commands.command(name="xp_reset_user", description="⚠️ Reseta o XP de um usuário (Admin).")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(membro="Usuário a ter o XP zerado")
    async def xp_reset_user(self, interaction: discord.Interaction, membro: discord.Member):
        self._set_xp(interaction.guild.id, membro.id, 0)
        await interaction.response.send_message(
            f"✅ XP de {membro.mention} resetado para 0.", ephemeral=True
        )

    @xp_reset_user.error
    async def _xp_reset_user_err(self, interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ Apenas administradores.", ephemeral=True)

    # ── /xp_reset_server ──────────────────────

    @app_commands.command(name="xp_reset_server", description="⚠️ Reseta o XP de TODOS no servidor (Admin).")
    @app_commands.checks.has_permissions(administrator=True)
    async def xp_reset_server(self, interaction: discord.Interaction):
        self.xp_data[str(interaction.guild.id)] = {}
        save_xp(self.xp_data)
        await interaction.response.send_message(
            "✅ XP de todo o servidor foi resetado.", ephemeral=True
        )

    @xp_reset_server.error
    async def _xp_reset_server_err(self, interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ Apenas administradores.", ephemeral=True)

    # ════════════════════════════════════════════
    #  GRUPO /xp_config  (todos admin-only)
    # ════════════════════════════════════════════

    xp_config = app_commands.Group(
        name="xp_config",
        description="Configurações do sistema de XP.",
        default_permissions=discord.Permissions(administrator=True),
    )

    # ver ──────────────────────────────────────
    @xp_config.command(name="ver", description="Mostra a configuração atual de XP.")
    async def cfg_ver(self, interaction: discord.Interaction):
        cfg = self._gcfg(interaction.guild.id)

        bl = ", ".join(f"<#{c}>" for c in cfg["blacklist"]) or "*(nenhum)*"
        lv_roles = "\n".join(
            f"  Nível **{k}** → <@&{v}>"
            for k, v in sorted(cfg["level_roles"].items(), key=lambda x: int(x[0]))
        ) or "*(nenhum)*"
        lv_ch = f"<#{cfg['levelup_channel']}>" if cfg["levelup_channel"] else "*(mesmo canal da msg)*"

        embed = discord.Embed(title="⚙️ Configuração de XP", color=discord.Color.from_rgb(120, 60, 240))
        embed.add_field(name="XP / mensagem", value=f"{cfg['xp_min']}–{cfg['xp_max']}", inline=True)
        embed.add_field(name="Cooldown",      value=f"{cfg['cooldown']}s",               inline=True)
        embed.add_field(name="Canal level-up",value=lv_ch,                               inline=True)
        embed.add_field(name="Lista negra",   value=bl,                                  inline=False)
        embed.add_field(name="Cargos por nível", value=lv_roles,                         inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # xp_por_mensagem ──────────────────────────
    @xp_config.command(name="xp_por_mensagem", description="Define o intervalo de XP ganho por mensagem.")
    @app_commands.describe(minimo="XP mínimo", maximo="XP máximo")
    async def cfg_xp_range(self, interaction: discord.Interaction, minimo: int, maximo: int):
        if minimo < 1 or maximo < minimo:
            await interaction.response.send_message("❌ Valores inválidos (mínimo ≥ 1, máximo ≥ mínimo).", ephemeral=True)
            return
        cfg = self._gcfg(interaction.guild.id)
        cfg["xp_min"] = minimo
        cfg["xp_max"] = maximo
        save_config(self.xp_cfg)
        await interaction.response.send_message(f"✅ XP por mensagem: **{minimo}–{maximo}**.", ephemeral=True)

    # cooldown ─────────────────────────────────
    @xp_config.command(name="cooldown", description="Define o cooldown entre ganhos de XP (segundos).")
    @app_commands.describe(segundos="Cooldown em segundos")
    async def cfg_cooldown(self, interaction: discord.Interaction, segundos: int):
        if segundos < 0:
            await interaction.response.send_message("❌ Valor inválido.", ephemeral=True)
            return
        cfg = self._gcfg(interaction.guild.id)
        cfg["cooldown"] = segundos
        save_config(self.xp_cfg)
        await interaction.response.send_message(f"✅ Cooldown: **{segundos}s**.", ephemeral=True)

    # canal_levelup ────────────────────────────
    @xp_config.command(name="canal_levelup", description="Define onde os anúncios de level-up são enviados.")
    @app_commands.describe(canal="Canal de texto (omita para usar o mesmo canal da mensagem)")
    async def cfg_levelup_ch(self, interaction: discord.Interaction, canal: discord.TextChannel = None):
        cfg = self._gcfg(interaction.guild.id)
        cfg["levelup_channel"] = canal.id if canal else None
        save_config(self.xp_cfg)
        msg = f"✅ Canal de level-up: {canal.mention}." if canal else "✅ Level-up anuncia no canal da mensagem."
        await interaction.response.send_message(msg, ephemeral=True)

    # add_cargo_nivel ──────────────────────────
    @xp_config.command(name="add_cargo_nivel", description="Concede um cargo ao atingir um nível.")
    @app_commands.describe(nivel="Nível necessário", cargo="Cargo a conceder")
    async def cfg_add_role(self, interaction: discord.Interaction, nivel: int, cargo: discord.Role):
        if nivel < 1:
            await interaction.response.send_message("❌ Nível deve ser ≥ 1.", ephemeral=True)
            return
        cfg = self._gcfg(interaction.guild.id)
        cfg["level_roles"][str(nivel)] = cargo.id
        save_config(self.xp_cfg)
        await interaction.response.send_message(
            f"✅ Nível **{nivel}** → {cargo.mention} configurado.", ephemeral=True
        )

    # remover_cargo_nivel ──────────────────────
    @xp_config.command(name="remover_cargo_nivel", description="Remove o cargo atribuído a um nível.")
    @app_commands.describe(nivel="Nível para remover o cargo")
    async def cfg_remove_role(self, interaction: discord.Interaction, nivel: int):
        cfg = self._gcfg(interaction.guild.id)
        if str(nivel) not in cfg.get("level_roles", {}):
            await interaction.response.send_message(f"❌ Nenhum cargo no nível {nivel}.", ephemeral=True)
            return
        del cfg["level_roles"][str(nivel)]
        save_config(self.xp_cfg)
        await interaction.response.send_message(f"✅ Cargo do nível **{nivel}** removido.", ephemeral=True)

    # blacklist_add ────────────────────────────
    @xp_config.command(name="blacklist_add", description="Adiciona um canal à lista negra (sem XP).")
    @app_commands.describe(canal="Canal a bloquear")
    async def cfg_bl_add(self, interaction: discord.Interaction, canal: discord.TextChannel):
        cfg = self._gcfg(interaction.guild.id)
        if canal.id in cfg["blacklist"]:
            await interaction.response.send_message("⚠️ Canal já está na lista negra.", ephemeral=True)
            return
        cfg["blacklist"].append(canal.id)
        save_config(self.xp_cfg)
        await interaction.response.send_message(f"✅ {canal.mention} adicionado à lista negra.", ephemeral=True)

    # blacklist_remover ────────────────────────
    @xp_config.command(name="blacklist_remover", description="Remove um canal da lista negra.")
    @app_commands.describe(canal="Canal a remover")
    async def cfg_bl_remove(self, interaction: discord.Interaction, canal: discord.TextChannel):
        cfg = self._gcfg(interaction.guild.id)
        if canal.id not in cfg["blacklist"]:
            await interaction.response.send_message("❌ Canal não está na lista negra.", ephemeral=True)
            return
        cfg["blacklist"].remove(canal.id)
        save_config(self.xp_cfg)
        await interaction.response.send_message(f"✅ {canal.mention} removido da lista negra.", ephemeral=True)


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(XP(bot))