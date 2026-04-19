import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import pathlib
import random
import time
import aiohttp
import io
import os
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

# ── Dificuldades: chave -> (nome exibido, multiplicador de XP necessário)
# Quanto maior o multiplicador, mais XP é exigido por nível → progressão mais lenta.
DIFFICULTIES: dict[str, tuple[str, float]] = {
    "facil":   ("🟢 Fácil",   0.60),
    "normal":  ("🔵 Normal",  1.00),
    "dificil": ("🟠 Difícil", 1.60),
    "expert":  ("🔴 Expert",  2.50),
    "insano":  ("💀 Insano",  4.00),
}

DEFAULT_CONFIG: dict = {
    "xp_min":          15,
    "xp_max":          25,
    "cooldown":        60,        # segundos entre ganhos de XP
    "blacklist":       [],        # IDs de canais ignorados
    "level_roles":     {},        # {"nivel": role_id}
    "levelup_channel": None,      # ID do canal de anúncios (None = mesmo canal)
    "difficulty":      "normal",  # chave de DIFFICULTIES
}


# ─────────────────────────────────────────────
#  MATEMÁTICA DE XP  ·  CURVA PROGRESSIVA
# ─────────────────────────────────────────────
def xp_to_next_level(level: int, diff_mult: float = 1.0) -> int:
    """
    XP necessário para sair do nível `level` e ir para `level + 1`.

    A fórmula combina um crescimento quadrático (base) com um fator de
    aceleração linear (steepness), garantindo que cada nível seja
    proporcionalmente mais caro que o anterior:

        base      = 5·L² + 50·L + 100
        steepness = 1 + 0.12·L         (cresce 12% a mais por nível)
        xp_needed = base × steepness × diff_mult

    Exemplos com dificuldade Normal (mult=1.0):
        L 0→1 :      100 XP
        L 9→10:    ~2 100 XP   (+21× em relação ao nível 0)
        L 19→20:   ~9 700 XP
        L 29→30:  ~26 500 XP   (≈260× em relação ao nível 0)
    """
    base      = 5 * (level ** 2) + 50 * level + 100
    steepness = 1.0 + level * 0.12
    return max(1, int(base * steepness * diff_mult))


def compute_level(total_xp: int, diff_mult: float = 1.0) -> tuple[int, int, int]:
    """Retorna (nivel_atual, xp_no_nivel_atual, xp_necessario_pro_proximo)."""
    level, accumulated = 0, 0
    while True:
        needed = xp_to_next_level(level, diff_mult)
        if accumulated + needed > total_xp:
            return level, total_xp - accumulated, needed
        accumulated += needed
        level += 1


# ─────────────────────────────────────────────
#  PERSISTÊNCIA  ·  ESCRITA ATÔMICA
# ─────────────────────────────────────────────
def _atomic_write(path: pathlib.Path, data: dict) -> None:
    """
    Salva `data` em `path` de forma atômica: escreve em arquivo temporário
    na mesma partição e só então substitui o original com os.replace().
    Isso garante que um desligamento abrupto nunca deixa o arquivo corrompido
    ou pela metade — ou o arquivo antigo existe intacto, ou o novo existe completo.
    """
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)   # operação atômica no nível do SO


def load_xp() -> dict:
    if XP_FILE.exists():
        try:
            return json.loads(XP_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # tenta o backup gerado pelo salvamento periódico
            bak = XP_FILE.with_suffix(".bak")
            if bak.exists():
                return json.loads(bak.read_text(encoding="utf-8"))
    return {}


def save_xp(data: dict) -> None:
    _atomic_write(XP_FILE, data)


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_config(data: dict) -> None:
    _atomic_write(CONFIG_FILE, data)


def get_guild_config(config: dict, guild_id: str) -> dict:
    cfg = config.setdefault(guild_id, {})
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


# ─────────────────────────────────────────────
#  GERAÇÃO DA RANK CARD  (inalterada)
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
    img  = Image.new("RGBA", (CARD_W, CARD_H), (15, 15, 25, 255))
    draw = ImageDraw.Draw(img)

    _rr(draw, (20, 18, CARD_W - 20, CARD_H - 18), 20, (25, 25, 40, 255))
    _rr(draw, (0, 0, 10, CARD_H), 5, (120, 60, 240, 255))

    av_r  = 88
    av_cx, av_cy = 125, CARD_H // 2
    av_url = str(member.display_avatar.replace(size=256, format="png").url)
    av_img = await _fetch_avatar(av_url)

    if av_img:
        av_circle = _circle_avatar(av_img, av_r * 2)
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

    f_rank    = _font(FONT_BOLD, 38)
    f_name    = _font(FONT_BOLD, 36)
    f_sub     = _font(FONT_REG,  20)
    f_nivel_l = _font(FONT_REG,  20)
    f_nivel_n = _font(FONT_BOLD, 30)
    f_xp      = _font(FONT_REG,  18)

    tx = 232

    rank_txt = f"#{rank}"
    draw.text((tx, 36), rank_txt, font=f_rank, fill=(120, 60, 240))
    rank_w = draw.textlength(rank_txt, font=f_rank)
    name_txt = member.display_name[:22]
    draw.text((tx + rank_w + 12, 40), name_txt, font=f_name, fill=(230, 230, 245))

    draw.text((tx, 90), "no servidor", font=f_sub, fill=(110, 110, 145))

    draw.text((tx, 128), "NÍVEL", font=f_nivel_l, fill=(120, 60, 240))
    draw.text((tx + 72, 120), str(level), font=f_nivel_n, fill=(230, 230, 245))

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
#  VIEW — LEADERBOARD PAGINADO
# ─────────────────────────────────────────────
_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
_LB_PER_PAGE = 10


def _mini_bar(cur: int, needed: int, width: int = 10) -> str:
    """Barra de progresso em caracteres Unicode."""
    if needed <= 0:
        return "▓" * width
    filled = max(0, min(width, int((cur / needed) * width)))
    return "▓" * filled + "░" * (width - filled)


class LeaderboardView(discord.ui.View):
    """
    View paginada para o /leaderboard.

    Exibe `_LB_PER_PAGE` entradas por página com navegação ◀/▶.
    O botão "Minha posição" leva o autor à página onde ele aparece.
    """

    def __init__(
        self,
        entries: list[tuple[discord.Member, int, int, int, int]],
        guild_name: str,
        author_id: int,
        diff_label: str,
    ):
        super().__init__(timeout=180)
        # entries: [(member, total_xp, level, xp_cur, xp_needed)]
        self.entries    = entries
        self.guild_name = guild_name
        self.author_id  = author_id
        self.diff_label = diff_label
        self.page       = 0
        self.total_pages = max(1, (len(entries) + _LB_PER_PAGE - 1) // _LB_PER_PAGE)
        self._refresh_buttons()

    # ── estado dos botões ─────────────────────

    def _refresh_buttons(self):
        self.btn_prev.disabled = (self.page == 0)
        self.btn_next.disabled = (self.page >= self.total_pages - 1)

    # ── construção do embed ───────────────────

    def build_embed(self) -> discord.Embed:
        start  = self.page * _LB_PER_PAGE
        slice_ = self.entries[start : start + _LB_PER_PAGE]

        embed = discord.Embed(
            title=f"🏆  Ranking de XP — {self.guild_name}",
            color=discord.Color.from_rgb(120, 60, 240),
        )

        lines: list[str] = []
        for i, (member, total, level, cur, needed) in enumerate(slice_):
            rank    = start + i + 1
            prefix  = _MEDALS.get(rank, f"`#{rank:>3}`")
            bar     = _mini_bar(cur, needed)
            pct     = int((cur / needed * 100)) if needed else 100
            name    = discord.utils.escape_markdown(member.display_name[:28])
            lines.append(
                f"{prefix} **{name}**\n"
                f"┗ Nv. **{level}** · {total:,} XP  {bar} {pct}%"
            )

        embed.description = "\n".join(lines) or "*Sem entradas nesta página.*"
        embed.set_footer(
            text=(
                f"Página {self.page + 1}/{self.total_pages}  ·  "
                f"{len(self.entries)} membros ranqueados  ·  "
                f"Dificuldade: {self.diff_label}"
            )
        )
        return embed

    # ── botões ────────────────────────────────

    @discord.ui.button(emoji="◀", style=discord.ButtonStyle.secondary, row=0)
    async def btn_prev(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        self.page -= 1
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(emoji="▶", style=discord.ButtonStyle.secondary, row=0)
    async def btn_next(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        self.page += 1
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Minha posição", emoji="📍", style=discord.ButtonStyle.primary, row=0)
    async def btn_mine(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        """Salta para a página onde o autor aparece no ranking."""
        for i, (m, *_) in enumerate(self.entries):
            if m.id == self.author_id:
                self.page = i // _LB_PER_PAGE
                self._refresh_buttons()
                await interaction.response.edit_message(embed=self.build_embed(), view=self)
                return
        await interaction.response.send_message(
            "Você ainda não tem XP registrado neste servidor.", ephemeral=True
        )


# ─────────────────────────────────────────────
#  COG
# ─────────────────────────────────────────────
class XP(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot     = bot
        self.xp_data = load_xp()
        self.xp_cfg  = load_config()
        self._cd: dict[str, float] = {}
        self._dirty  = False   # flag: há dados não salvos em disco?
        self._periodic_save.start()

    def cog_unload(self):
        """Garante salvamento ao descarregar o Cog (manutenção, reload, shutdown)."""
        self._periodic_save.cancel()
        if self._dirty:
            save_xp(self.xp_data)
            save_config(self.xp_cfg)

    # ── salvamento periódico (backup a cada 5 min) ─

    @tasks.loop(minutes=5)
    async def _periodic_save(self):
        if self._dirty:
            save_xp(self.xp_data)
            # cria .bak como camada extra de segurança
            bak = XP_FILE.with_suffix(".bak")
            bak.write_text(json.dumps(self.xp_data, indent=2), encoding="utf-8")
            self._dirty = False

    @_periodic_save.before_loop
    async def _before_save(self):
        await self.bot.wait_until_ready()

    # ── helpers internos ──────────────────────

    def _gcfg(self, guild_id: int) -> dict:
        return get_guild_config(self.xp_cfg, str(guild_id))

    def _diff_mult(self, guild_id: int) -> float:
        key = self._gcfg(guild_id).get("difficulty", "normal")
        return DIFFICULTIES.get(key, DIFFICULTIES["normal"])[1]

    def _diff_label(self, guild_id: int) -> str:
        key = self._gcfg(guild_id).get("difficulty", "normal")
        return DIFFICULTIES.get(key, DIFFICULTIES["normal"])[0]

    def _get_xp(self, guild_id: int, user_id: int) -> int:
        return self.xp_data.get(str(guild_id), {}).get(str(user_id), {}).get("xp", 0)

    def _set_xp(self, guild_id: int, user_id: int, xp: int):
        gid, uid = str(guild_id), str(user_id)
        self.xp_data.setdefault(gid, {}).setdefault(uid, {})["xp"] = max(0, xp)
        self._dirty = True
        save_xp(self.xp_data)   # salva imediatamente (atomicamente)

    def _leaderboard(
        self, guild: discord.Guild
    ) -> list[tuple[discord.Member, int, int, int, int]]:
        """
        Retorna a lista classificada de membros com XP.
        Cada entrada: (member, total_xp, level, xp_cur, xp_needed).
        """
        raw      = self.xp_data.get(str(guild.id), {})
        mult     = self._diff_mult(guild.id)
        result   = []
        for uid, data in raw.items():
            m = guild.get_member(int(uid))
            if m and not m.bot:
                total             = data.get("xp", 0)
                lvl, cur, needed  = compute_level(total, mult)
                result.append((m, total, lvl, cur, needed))
        result.sort(key=lambda x: x[1], reverse=True)
        return result

    def _rank_of(self, guild: discord.Guild, user_id: int) -> int:
        for i, (m, *_) in enumerate(self._leaderboard(guild), 1):
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

        mult    = self._diff_mult(message.guild.id)
        old_lvl, _, _ = compute_level(old_xp, mult)
        new_lvl, _, _ = compute_level(new_xp, mult)

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

    # ══════════════════════════════════════════
    #  COMANDOS PÚBLICOS
    # ══════════════════════════════════════════

    # ── /rank ─────────────────────────────────

    @app_commands.command(
        name="rank",
        description="Exibe a sua carta de XP com nível, posição no servidor e progresso atual.",
    )
    @app_commands.describe(membro="Membro cujo rank você quer ver (padrão: você mesmo)")
    async def rank(self, interaction: discord.Interaction, membro: discord.Member = None):
        await interaction.response.defer()
        target = membro or interaction.user
        if target.bot:
            await interaction.followup.send("❌ Bots não têm XP.", ephemeral=True)
            return

        total            = self._get_xp(interaction.guild.id, target.id)
        mult             = self._diff_mult(interaction.guild.id)
        lvl, cur, needed = compute_level(total, mult)
        rank             = self._rank_of(interaction.guild, target.id)

        card = await generate_rank_card(target, total, rank, lvl, cur, needed)
        await interaction.followup.send(file=card)

    # ── /leaderboard ──────────────────────────

    @app_commands.command(
        name="leaderboard",
        description="Exibe o ranking completo de XP do servidor, paginado (10 por página).",
    )
    async def leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer()
        lb = self._leaderboard(interaction.guild)

        if not lb:
            await interaction.followup.send("Ninguém tem XP ainda!", ephemeral=True)
            return

        view = LeaderboardView(
            entries    = lb,
            guild_name = interaction.guild.name,
            author_id  = interaction.user.id,
            diff_label = self._diff_label(interaction.guild.id),
        )
        await interaction.followup.send(embed=view.build_embed(), view=view)

    # ══════════════════════════════════════════
    #  GRUPO /xp_admin  —  ações destrutivas
    #  Requer permissão de Administrador.
    # ══════════════════════════════════════════

    xp_admin = app_commands.Group(
        name="xp_admin",
        description="Ações administrativas destrutivas do sistema de XP (requer Administrador).",
        default_permissions=discord.Permissions(administrator=True),
    )

    @xp_admin.command(
        name="reset_usuario",
        description="Zera todo o XP de um usuário específico, sem possibilidade de desfazer.",
    )
    @app_commands.describe(membro="Usuário que terá o XP zerado")
    async def xp_reset_user(self, interaction: discord.Interaction, membro: discord.Member):
        self._set_xp(interaction.guild.id, membro.id, 0)
        await interaction.response.send_message(
            f"✅ XP de {membro.mention} zerado.", ephemeral=True
        )

    @xp_admin.command(
        name="reset_servidor",
        description="Zera o XP de TODOS os membros do servidor. Ação irreversível.",
    )
    async def xp_reset_server(self, interaction: discord.Interaction):
        self.xp_data[str(interaction.guild.id)] = {}
        save_xp(self.xp_data)
        self._dirty = False
        await interaction.response.send_message(
            "✅ XP de todos os membros do servidor foi zerado.", ephemeral=True
        )

    # ══════════════════════════════════════════
    #  GRUPO /xp_config  —  configurações
    #  Requer permissão de Administrador.
    # ══════════════════════════════════════════

    xp_config = app_commands.Group(
        name="xp_config",
        description="Configurações do sistema de XP (requer Administrador).",
        default_permissions=discord.Permissions(administrator=True),
    )

    # ── ver ───────────────────────────────────

    @xp_config.command(
        name="ver",
        description="Mostra todas as configurações atuais de XP deste servidor.",
    )
    async def cfg_ver(self, interaction: discord.Interaction):
        cfg = self._gcfg(interaction.guild.id)

        bl       = ", ".join(f"<#{c}>" for c in cfg["blacklist"]) or "*(nenhum)*"
        lv_roles = "\n".join(
            f"  Nível **{k}** → <@&{v}>"
            for k, v in sorted(cfg["level_roles"].items(), key=lambda x: int(x[0]))
        ) or "*(nenhum)*"
        lv_ch    = f"<#{cfg['levelup_channel']}>" if cfg["levelup_channel"] else "*(mesmo canal da mensagem)*"
        diff_key = cfg.get("difficulty", "normal")
        diff_lbl = DIFFICULTIES.get(diff_key, DIFFICULTIES["normal"])[0]

        embed = discord.Embed(title="⚙️ Configuração de XP", color=discord.Color.from_rgb(120, 60, 240))
        embed.add_field(name="XP por mensagem", value=f"{cfg['xp_min']}–{cfg['xp_max']}",  inline=True)
        embed.add_field(name="Cooldown",        value=f"{cfg['cooldown']}s",                 inline=True)
        embed.add_field(name="Dificuldade",     value=diff_lbl,                              inline=True)
        embed.add_field(name="Canal de level-up", value=lv_ch,                              inline=False)
        embed.add_field(name="Canais bloqueados",  value=bl,                                 inline=False)
        embed.add_field(name="Cargos por nível",   value=lv_roles,                          inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── xp_por_mensagem ───────────────────────

    @xp_config.command(
        name="xp_por_mensagem",
        description="Define a quantidade mínima e máxima de XP ganha por mensagem.",
    )
    @app_commands.describe(minimo="XP mínimo (≥ 1)", maximo="XP máximo (≥ mínimo)")
    async def cfg_xp_range(self, interaction: discord.Interaction, minimo: int, maximo: int):
        if minimo < 1 or maximo < minimo:
            await interaction.response.send_message(
                "❌ Valores inválidos. O mínimo deve ser ≥ 1 e o máximo ≥ mínimo.", ephemeral=True
            )
            return
        cfg = self._gcfg(interaction.guild.id)
        cfg["xp_min"] = minimo
        cfg["xp_max"] = maximo
        save_config(self.xp_cfg)
        await interaction.response.send_message(
            f"✅ XP por mensagem: **{minimo}–{maximo}**.", ephemeral=True
        )

    # ── cooldown ──────────────────────────────

    @xp_config.command(
        name="cooldown",
        description="Define o intervalo mínimo (em segundos) entre dois ganhos de XP do mesmo usuário.",
    )
    @app_commands.describe(segundos="Cooldown em segundos (0 = sem cooldown)")
    async def cfg_cooldown(self, interaction: discord.Interaction, segundos: int):
        if segundos < 0:
            await interaction.response.send_message("❌ O valor deve ser ≥ 0.", ephemeral=True)
            return
        cfg = self._gcfg(interaction.guild.id)
        cfg["cooldown"] = segundos
        save_config(self.xp_cfg)
        await interaction.response.send_message(f"✅ Cooldown definido para **{segundos}s**.", ephemeral=True)

    # ── dificuldade ───────────────────────────

    @xp_config.command(
        name="dificuldade",
        description="Define a dificuldade de progressão de XP. Afeta somente a curva de nível — não o XP já acumulado.",
    )
    @app_commands.describe(nivel="Dificuldade desejada")
    @app_commands.choices(nivel=[
        app_commands.Choice(name="🟢 Fácil   — 40% menos XP exigido por nível",  value="facil"),
        app_commands.Choice(name="🔵 Normal  — Padrão do sistema",                value="normal"),
        app_commands.Choice(name="🟠 Difícil — 60% mais XP por nível",            value="dificil"),
        app_commands.Choice(name="🔴 Expert  — 2,5× mais XP por nível",           value="expert"),
        app_commands.Choice(name="💀 Insano  — 4× mais XP por nível",             value="insano"),
    ])
    async def cfg_difficulty(self, interaction: discord.Interaction, nivel: str):
        cfg = self._gcfg(interaction.guild.id)
        cfg["difficulty"] = nivel
        save_config(self.xp_cfg)
        label = DIFFICULTIES[nivel][0]
        await interaction.response.send_message(
            f"✅ Dificuldade definida para **{label}**.\n"
            f"ℹ️ O XP acumulado não foi alterado; apenas o cálculo de nível muda.",
            ephemeral=True,
        )

    # ── canal_levelup ─────────────────────────

    @xp_config.command(
        name="canal_levelup",
        description="Define em qual canal os anúncios de level-up serão enviados.",
    )
    @app_commands.describe(canal="Canal de texto desejado (omita para usar o mesmo canal da mensagem)")
    async def cfg_levelup_ch(self, interaction: discord.Interaction, canal: discord.TextChannel = None):
        cfg = self._gcfg(interaction.guild.id)
        cfg["levelup_channel"] = canal.id if canal else None
        save_config(self.xp_cfg)
        msg = (
            f"✅ Anúncios de level-up serão enviados em {canal.mention}."
            if canal else
            "✅ Anúncios de level-up serão enviados no mesmo canal da mensagem."
        )
        await interaction.response.send_message(msg, ephemeral=True)

    # ── cargo_add ─────────────────────────────

    @xp_config.command(
        name="cargo_add",
        description="Configura um cargo a ser concedido automaticamente ao atingir um nível.",
    )
    @app_commands.describe(nivel="Nível que aciona a concessão (≥ 1)", cargo="Cargo a ser atribuído")
    async def cfg_add_role(self, interaction: discord.Interaction, nivel: int, cargo: discord.Role):
        if nivel < 1:
            await interaction.response.send_message("❌ O nível deve ser ≥ 1.", ephemeral=True)
            return
        cfg = self._gcfg(interaction.guild.id)
        cfg["level_roles"][str(nivel)] = cargo.id
        save_config(self.xp_cfg)
        await interaction.response.send_message(
            f"✅ Ao atingir o nível **{nivel}**, {cargo.mention} será concedido automaticamente.",
            ephemeral=True,
        )

    # ── cargo_remover ─────────────────────────

    @xp_config.command(
        name="cargo_remover",
        description="Remove a associação de um cargo a um nível específico.",
    )
    @app_commands.describe(nivel="Nível cuja associação de cargo será removida")
    async def cfg_remove_role(self, interaction: discord.Interaction, nivel: int):
        cfg = self._gcfg(interaction.guild.id)
        if str(nivel) not in cfg.get("level_roles", {}):
            await interaction.response.send_message(
                f"❌ Nenhum cargo está associado ao nível {nivel}.", ephemeral=True
            )
            return
        del cfg["level_roles"][str(nivel)]
        save_config(self.xp_cfg)
        await interaction.response.send_message(
            f"✅ Associação de cargo do nível **{nivel}** removida.", ephemeral=True
        )

    # ── blacklist_add ─────────────────────────

    @xp_config.command(
        name="blacklist_add",
        description="Impede que um canal conceda XP (mensagens no canal são ignoradas).",
    )
    @app_commands.describe(canal="Canal a ser bloqueado")
    async def cfg_bl_add(self, interaction: discord.Interaction, canal: discord.TextChannel):
        cfg = self._gcfg(interaction.guild.id)
        if canal.id in cfg["blacklist"]:
            await interaction.response.send_message("⚠️ Esse canal já está bloqueado.", ephemeral=True)
            return
        cfg["blacklist"].append(canal.id)
        save_config(self.xp_cfg)
        await interaction.response.send_message(
            f"✅ {canal.mention} adicionado à lista de canais bloqueados (sem XP).", ephemeral=True
        )

    # ── blacklist_remover ─────────────────────

    @xp_config.command(
        name="blacklist_remover",
        description="Permite que um canal volte a conceder XP (remove o bloqueio).",
    )
    @app_commands.describe(canal="Canal a ser desbloqueado")
    async def cfg_bl_remove(self, interaction: discord.Interaction, canal: discord.TextChannel):
        cfg = self._gcfg(interaction.guild.id)
        if canal.id not in cfg["blacklist"]:
            await interaction.response.send_message(
                "❌ Esse canal não está bloqueado.", ephemeral=True
            )
            return
        cfg["blacklist"].remove(canal.id)
        save_config(self.xp_cfg)
        await interaction.response.send_message(
            f"✅ {canal.mention} desbloqueado — voltará a conceder XP.", ephemeral=True
        )


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(XP(bot))