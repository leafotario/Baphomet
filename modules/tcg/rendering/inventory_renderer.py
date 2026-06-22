"""
Motor Gráfico de Renderização do Inventário TCG — Baphomet Bot.

Renderiza uma grade 3×3 de mini-cartas sobre um background Glassmorphism
gerado proceduralmente (sem assets externos de textura). Cada mini-carta
replica fielmente o design visual do ``BoosterGraphicEngine``, apenas em
escala reduzida, garantindo consistência estética absoluta.

Pipeline: aiosqlite → Pillow (CPU-bound em thread) → io.BytesIO → Discord.
"""

from __future__ import annotations

import io
import math
import os
import logging
from typing import Tuple, List, Dict, Any

import aiosqlite
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes de Layout do Canvas Principal
# ---------------------------------------------------------------------------
CANVAS_W, CANVAS_H = 1600, 1200
CANVAS_RADIUS = 30

HEADER_H = 150
PADDING_X = 50
PADDING_Y = 40

GRID_COLS = 3
GRID_ROWS = 3
CARDS_PER_PAGE = GRID_COLS * GRID_ROWS  # 9

# Dimensões de cada Mini-Carta (alta resolução antes do downscale)
MINI_CARD_W, MINI_CARD_H = 400, 580
MINI_CARD_RADIUS = 22

# ---------------------------------------------------------------------------
# Paleta de Cores do Sistema
# ---------------------------------------------------------------------------
COLOR_BG_DARK = (12, 10, 18, 255)
COLOR_BG_PANEL = (18, 15, 28, 255)
COLOR_GOLD = (255, 215, 0, 255)
COLOR_WHITE = (255, 255, 255, 255)
COLOR_GRAY = (170, 170, 180, 255)
COLOR_DIM = (100, 100, 110, 255)

# Cores por stat
COLOR_ATK = (255, 60, 60, 255)
COLOR_DEF = (80, 180, 255, 255)
COLOR_SPD = (255, 220, 50, 255)

# Mapa de cores de raridade
RARITY_COLORS: Dict[str, Tuple[int, int, int, int]] = {
    "Comum":     (140, 140, 150, 255),
    "Raro":      (60, 130, 255, 255),
    "Épico":     (180, 60, 255, 255),
    "Lendário":  (255, 180, 0, 255),
    "Mítico":    (255, 50, 50, 255),
}


class InventoryRenderer:
    """
    Motor de renderização assíncrono para o painel de inventário TCG.

    Conecta diretamente ao banco ``aiosqlite``, busca cartas paginadas e
    gera uma imagem PNG de alta qualidade em buffer de bytes.

    Parameters
    ----------
    db_path : str
        Caminho para o arquivo SQLite do TCG.
    fonts_path : str
        Diretório contendo as fontes TTF/OTF.
    """

    def __init__(
        self,
        db_path: str = "data/baphomet_tcg.db",
        fonts_path: str = "assets/fonts/",
    ) -> None:
        self.db_path = db_path
        self.fonts_path = fonts_path
        self._load_fonts()

    # ------------------------------------------------------------------
    # Font Management
    # ------------------------------------------------------------------

    def _load_fonts(self) -> None:
        pairs = {
            # Canvas header fonts
            "font_title":        ("Montserrat-Black.ttf",   55),
            "font_subtitle":     ("Poppins-Regular.ttf",    24),
            "font_page":         ("Montserrat-Black.ttf",   35),
            # Mini-card fonts
            "font_card_name":    ("Montserrat-Black.ttf",   22),
            "font_card_rarity":  ("Poppins-Bold.ttf",       14),
            "font_card_label":   ("Poppins-Bold.ttf",       12),
            "font_card_stat":    ("Montserrat-Black.ttf",   30),
            "font_card_passive": ("Poppins-Bold.ttf",       11),
            # Empty state
            "font_empty":        ("Montserrat-Black.ttf",   40),
        }
        for attr, (filename, size) in pairs.items():
            path = os.path.join(self.fonts_path, filename)
            try:
                setattr(self, attr, ImageFont.truetype(path, size=size))
            except OSError:
                logger.warning("Fonte '%s' não encontrada, usando fallback.", path)
                setattr(self, attr, ImageFont.load_default())

    # ------------------------------------------------------------------
    # Database Access Layer
    # ------------------------------------------------------------------

    async def _fetch_inventory_page(
        self, usuario_id: str, pagina: int
    ) -> Tuple[List[Dict[str, Any]], int, int]:
        """
        Busca as cartas da página solicitada e calcula o total de páginas.

        Returns
        -------
        (cards, current_page, total_pages)
        """
        offset = pagina * CARDS_PER_PAGE

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # Total de cartas do jogador
            cursor = await db.execute(
                "SELECT COUNT(*) as total FROM card_instances WHERE dono_id = ?",
                (usuario_id,),
            )
            total_row = await cursor.fetchone()
            total_cards = total_row["total"] if total_row else 0
            total_pages = max(1, math.ceil(total_cards / CARDS_PER_PAGE))

            # Clamp da página
            safe_page = max(0, min(pagina, total_pages - 1))
            offset = safe_page * CARDS_PER_PAGE

            # Fetch com JOIN
            cursor = await db.execute(
                """
                SELECT
                    i.uuid, i.atk, i.defesa, i.spd, i.passiva,
                    t.nome_moldura AS nome, t.raridade
                FROM card_instances i
                JOIN card_templates t ON i.modelo_id = t.id_serial
                WHERE i.dono_id = ?
                ORDER BY i.rowid DESC
                LIMIT ? OFFSET ?
                """,
                (usuario_id, CARDS_PER_PAGE, offset),
            )
            rows = await cursor.fetchall()
            cards = [dict(r) for r in rows]

        return cards, safe_page, total_pages

    # ------------------------------------------------------------------
    # Primitivas Gráficas
    # ------------------------------------------------------------------

    @staticmethod
    def _rounded_rect_mask(size: Tuple[int, int], radius: int) -> Image.Image:
        ss = 4
        big = (size[0] * ss, size[1] * ss)
        mask = Image.new("L", big, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, big[0] - 1, big[1] - 1), radius=radius * ss, fill=255,
        )
        return mask.resize(size, Image.Resampling.LANCZOS)

    @staticmethod
    def _draw_centered_text(
        draw: ImageDraw.ImageDraw,
        box: Tuple[int, int, int, int],
        text: str,
        font: ImageFont.FreeTypeFont,
        fill: Tuple[int, int, int, int],
    ) -> None:
        """Centralização matemática perfeita via getbbox."""
        left, top, right, bottom = font.getbbox(text)
        tw = right - left
        th = bottom - top

        bw = box[2] - box[0]
        bh = box[3] - box[1]

        tx = box[0] + (bw - tw) / 2
        ty = box[1] + (bh - th) / 2 - top
        draw.text((tx, ty), text, font=font, fill=fill)

    def _draw_recessed_box(
        self,
        canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
        box: Tuple[int, int, int, int],
        radius: int,
        fill: Tuple[int, int, int, int],
    ) -> None:
        """Caixa com ranhura física (sombra TL, luz BR)."""
        draw.rounded_rectangle(box, radius=radius, fill=fill)

        w, h = canvas.size
        clip = Image.new("L", (w, h), 0)
        ImageDraw.Draw(clip).rounded_rectangle(box, radius=radius, fill=255)

        # Sombra Top-Left
        sl = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sb = (box[0] - 2, box[1] - 2, box[2] - 2, box[3] - 2)
        ImageDraw.Draw(sl).rounded_rectangle(sb, radius=radius, outline=(0, 0, 0, 200), width=3)
        sl = sl.filter(ImageFilter.GaussianBlur(2))
        cl = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        cl.paste(sl, (0, 0), clip)
        canvas.alpha_composite(cl)

        # Luz Bottom-Right
        ll = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        lb = (box[0] + 2, box[1] + 2, box[2] + 2, box[3] + 2)
        ImageDraw.Draw(ll).rounded_rectangle(lb, radius=radius, outline=(255, 255, 255, 80), width=3)
        ll = ll.filter(ImageFilter.GaussianBlur(2))
        cl2 = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        cl2.paste(ll, (0, 0), clip)
        canvas.alpha_composite(cl2)

    # ------------------------------------------------------------------
    # Renderização de Mini-Carta Individual
    # ------------------------------------------------------------------

    def _render_mini_card(self, card: Dict[str, Any]) -> Image.Image:
        """
        Renderiza uma mini-carta individual no estilo exato do BoosterGraphicEngine,
        porém em escala reduzida para encaixar na grade 3×3.
        """
        cw, ch = MINI_CARD_W, MINI_CARD_H
        cr = MINI_CARD_RADIUS

        canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)

        nome = card.get("nome", "???")
        raridade = card.get("raridade", "Comum")
        atk = card.get("atk", 0)
        defesa = card.get("defesa", 0)
        spd = card.get("spd", 0)
        passiva = card.get("passiva", "nenhuma")
        uuid_str = card.get("uuid", "00000000")[:8]

        rarity_color = RARITY_COLORS.get(raridade, RARITY_COLORS["Comum"])

        # --- Background Glassmorphism escuro ---
        bg_base = (rarity_color[0] // 6, rarity_color[1] // 6, rarity_color[2] // 6, 255)
        bg_top = (bg_base[0] + 15, bg_base[1] + 15, bg_base[2] + 15, 255)

        # Gradiente vertical
        for y in range(ch):
            t = y / max(ch - 1, 1)
            r = int(bg_top[0] + (bg_base[0] - bg_top[0]) * t)
            g = int(bg_top[1] + (bg_base[1] - bg_top[1]) * t)
            b = int(bg_top[2] + (bg_base[2] - bg_top[2]) * t)
            draw.line([(0, y), (cw, y)], fill=(r, g, b, 255))

        # Aplicar máscara arredondada
        mask = self._rounded_rect_mask((cw, ch), cr)
        bg_layer = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        bg_layer.paste(canvas, (0, 0), mask)
        canvas = bg_layer
        draw = ImageDraw.Draw(canvas)

        # Borda luminosa
        draw.rounded_rectangle(
            (0, 0, cw - 1, ch - 1), radius=cr,
            outline=(*rarity_color[:3], 100), width=2,
        )

        # --- Header: Nome + Raridade ---
        header_h = 80
        # Nome da carta
        name_text = nome.upper()
        if len(name_text) > 18:
            name_text = name_text[:17] + "…"
        name_box = (10, 12, cw - 10, 42)
        self._draw_centered_text(draw, name_box, name_text, self.font_card_name, COLOR_WHITE)

        # Pílula de raridade
        rar_text = raridade.upper()
        rl, rt, rr, rb = self.font_card_rarity.getbbox(rar_text)
        pill_w = (rr - rl) + 30
        pill_h = 24
        pill_x = (cw - pill_w) // 2
        pill_y = 48
        pill_box = (pill_x, pill_y, pill_x + pill_w, pill_y + pill_h)

        draw.rounded_rectangle(
            pill_box, radius=pill_h // 2,
            fill=(rarity_color[0] // 3, rarity_color[1] // 3, rarity_color[2] // 3, 200),
            outline=(*rarity_color[:3], 60), width=1,
        )
        self._draw_centered_text(draw, pill_box, rar_text, self.font_card_rarity, COLOR_WHITE)

        # --- Passive icon (canto superior direito) ---
        if passiva and passiva != "nenhuma":
            star_size = 22
            sx, sy = cw - star_size - 10, 12
            draw.rounded_rectangle(
                (sx, sy, sx + star_size, sy + star_size), radius=4,
                fill=(255, 200, 0, 200),
            )
            self._draw_centered_text(
                draw, (sx, sy, sx + star_size, sy + star_size),
                "P", self.font_card_passive, (0, 0, 0, 255),
            )

        # --- UUID badge (bottom-left flair) ---
        uuid_y = header_h
        draw.text((14, uuid_y), f"#{uuid_str}", font=self.font_card_passive, fill=COLOR_DIM)

        # --- Central Area (decorative line separator) ---
        sep_y = header_h + 16
        draw.line([(20, sep_y), (cw - 20, sep_y)], fill=(*rarity_color[:3], 50), width=1)

        # --- Giant Stat Display (centro da carta) ---
        stat_area_top = sep_y + 16
        stat_area_h = ch - stat_area_top - 10
        stat_block_h = stat_area_h // 3

        stats = [
            ("ATK", str(atk), COLOR_ATK),
            ("DEF", str(defesa), COLOR_DEF),
            ("SPD", str(spd), COLOR_SPD),
        ]

        for i, (label, val, color) in enumerate(stats):
            by = stat_area_top + i * stat_block_h
            stat_box = (16, by + 4, cw - 16, by + stat_block_h - 4)

            # Caixa recessed
            self._draw_recessed_box(
                canvas, draw, stat_box, 12,
                fill=(0, 0, 0, 130),
            )

            # Label esquerdo
            label_box = (stat_box[0], stat_box[1], stat_box[0] + 80, stat_box[3])
            self._draw_centered_text(draw, label_box, label, self.font_card_label, COLOR_GRAY)

            # Separador vertical
            sep_x = stat_box[0] + 80
            draw.line(
                [(sep_x, stat_box[1] + 8), (sep_x, stat_box[3] - 8)],
                fill=(255, 255, 255, 30), width=1,
            )

            # Valor centralizado na direita
            val_box = (sep_x + 4, stat_box[1], stat_box[2], stat_box[3])
            self._draw_centered_text(draw, val_box, val, self.font_card_stat, color)

        return canvas

    # ------------------------------------------------------------------
    # Canvas Principal (Composição)
    # ------------------------------------------------------------------

    def _render_canvas(
        self,
        cards: List[Dict[str, Any]],
        usuario_id: str,
        current_page: int,
        total_pages: int,
    ) -> Image.Image:
        """Compõe o canvas completo com header + grade de mini-cartas."""
        canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), COLOR_BG_DARK)
        draw = ImageDraw.Draw(canvas)

        # --- Background sutil ---
        # Gradiente radial simulado (vignette escura)
        vignette = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
        vg_draw = ImageDraw.Draw(vignette)
        cx, cy = CANVAS_W // 2, CANVAS_H // 2
        max_r = int((CANVAS_W**2 + CANVAS_H**2) ** 0.5 / 2)
        for i in range(0, max_r, 4):
            alpha = min(255, int(i / max_r * 80))
            vg_draw.ellipse(
                (cx - max_r + i, cy - max_r + i, cx + max_r - i, cy + max_r - i),
                fill=(0, 0, 0, 0), outline=(12, 8, 20, alpha),
            )
        canvas.alpha_composite(vignette)

        # Borda da carta do canvas
        draw.rounded_rectangle(
            (0, 0, CANVAS_W - 1, CANVAS_H - 1), radius=CANVAS_RADIUS,
            outline=(255, 255, 255, 25), width=2,
        )

        # --- Header (150px) ---
        # Título
        draw.text(
            (PADDING_X, PADDING_Y),
            "INVENTÁRIO // COLEÇÃO",
            font=self.font_title, fill=COLOR_WHITE,
        )
        # Subtítulo ID
        draw.text(
            (PADDING_X, PADDING_Y + 65),
            f"ID_RESONANCE // {usuario_id}",
            font=self.font_subtitle, fill=COLOR_GRAY,
        )
        # Paginação (alinhado à direita)
        page_text = f"[ PÁGINA {current_page + 1} / {total_pages} ]"
        pl, pt, pr, pb = self.font_page.getbbox(page_text)
        page_x = CANVAS_W - PADDING_X - (pr - pl)
        draw.text((page_x, PADDING_Y + 15), page_text, font=self.font_page, fill=COLOR_GOLD)

        # Linha separadora do header
        sep_y = HEADER_H + PADDING_Y
        draw.line(
            [(PADDING_X, sep_y), (CANVAS_W - PADDING_X, sep_y)],
            fill=(255, 255, 255, 30), width=1,
        )

        # --- Grade 3×3 ---
        grid_top = sep_y + 20
        grid_available_w = CANVAS_W - (PADDING_X * 2)
        grid_available_h = CANVAS_H - grid_top - PADDING_Y

        # Calcular tamanho da célula
        gap = 30
        cell_w = (grid_available_w - gap * (GRID_COLS - 1)) // GRID_COLS
        cell_h = (grid_available_h - gap * (GRID_ROWS - 1)) // GRID_ROWS

        for idx, card in enumerate(cards):
            col = idx % GRID_COLS
            row = idx // GRID_COLS

            cell_x = PADDING_X + col * (cell_w + gap)
            cell_y = grid_top + row * (cell_h + gap)

            # Renderizar mini-carta em alta resolução
            mini_card = self._render_mini_card(card)

            # Redimensionar para encaixar na célula com LANCZOS
            mini_card = mini_card.resize(
                (cell_w, cell_h), Image.Resampling.LANCZOS,
            )

            # Drop shadow sutil
            shadow = Image.new("RGBA", (cell_w + 12, cell_h + 12), (0, 0, 0, 0))
            sd = ImageDraw.Draw(shadow)
            sd.rounded_rectangle(
                (6, 8, cell_w + 6, cell_h + 8), radius=16,
                fill=(0, 0, 0, 100),
            )
            shadow = shadow.filter(ImageFilter.GaussianBlur(8))
            canvas.alpha_composite(shadow, (cell_x - 6, cell_y - 4))

            # Colar a carta
            canvas.alpha_composite(mini_card, (cell_x, cell_y))

        return canvas

    def _render_empty_state(self, usuario_id: str) -> Image.Image:
        """Renderiza o estado vazio quando o inventário não possui cartas."""
        canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), COLOR_BG_DARK)
        draw = ImageDraw.Draw(canvas)

        draw.rounded_rectangle(
            (0, 0, CANVAS_W - 1, CANVAS_H - 1), radius=CANVAS_RADIUS,
            outline=(255, 255, 255, 25), width=2,
        )

        # Mensagem central
        msg = "NENHUM ATIVO DETECTADO NA FORJA"
        box = (0, 0, CANVAS_W, CANVAS_H)
        self._draw_centered_text(draw, box, msg, self.font_empty, COLOR_DIM)

        # ID embaixo
        sub = f"ID // {usuario_id}"
        sl, st, sr, sb = self.font_subtitle.getbbox(sub)
        sub_x = (CANVAS_W - (sr - sl)) // 2
        draw.text((sub_x, CANVAS_H // 2 + 50), sub, font=self.font_subtitle, fill=COLOR_GRAY)

        return canvas

    # ------------------------------------------------------------------
    # Ponto de Entrada Público
    # ------------------------------------------------------------------

    async def gerar_imagem_inventario(
        self,
        usuario_id: str,
        pagina: int = 0,
    ) -> io.BytesIO:
        """
        Busca as cartas paginadas e renderiza a grade completa.

        Parameters
        ----------
        usuario_id : str
            ID do jogador (Discord).
        pagina : int
            Número da página (0-indexed).

        Returns
        -------
        io.BytesIO
            Buffer PNG pronto para envio.
        """
        # 1. Fetch do banco de dados
        cards, current_page, total_pages = await self._fetch_inventory_page(
            usuario_id, pagina,
        )

        # 2. Renderização (CPU-bound)
        if not cards:
            img = self._render_empty_state(str(usuario_id))
        else:
            img = self._render_canvas(cards, str(usuario_id), current_page, total_pages)

        # 3. Aplicar máscara arredondada final
        final_mask = self._rounded_rect_mask((CANVAS_W, CANVAS_H), CANVAS_RADIUS)
        output = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
        output.paste(img, (0, 0), final_mask)

        # 4. Exportar para buffer
        buffer = io.BytesIO()
        output.save(buffer, format="PNG", compress_level=6)
        buffer.seek(0)

        # Cleanup
        img.close()
        output.close()

        return buffer


# ---------------------------------------------------------------------------
# Função Autônoma (compatível com a assinatura solicitada)
# ---------------------------------------------------------------------------

async def gerar_imagem_inventario(
    db_path: str,
    usuario_id: str,
    pagina: int,
) -> io.BytesIO:
    """
    Wrapper top-level que instancia o renderer e executa a renderização.

    Essa é a assinatura exata solicitada na especificação técnica.
    """
    renderer = InventoryRenderer(db_path=db_path)
    return await renderer.gerar_imagem_inventario(usuario_id, pagina)
