"""
Motor Gráfico de Renderização do Inventário TCG — Baphomet Bot.
=================================================================

Renderiza uma grade 3×3 de cartas estilizadas sobre um canvas escuro de alta
resolução. Cada carta replica fielmente o design de referência:

    ┌─────────────────────┐
    │  ┌───────────────┐  │   Avatar (cantos arredondados)
    │  │               │  │
    │  └───────────────┘  │
    │       ┌──────┐      │   Pill badge de raridade
    │       │COMUM │      │
    │       └──────┘      │
    │       ICHIRO        │   Nome (caixa alta, centralizado)
    │  ┌────┬────┬────┐   │
    │  │ATK │DEF │SPD │   │   Stats (3 colunas horizontais)
    │  │ 5  │ 50 │ 1  │   │
    │  └────┴────┴────┘   │
    └─────────────────────┘

Pipeline: aiosqlite → Pillow (CPU-bound) → io.BytesIO → discord.File
"""

from __future__ import annotations

import io
import math
import os
import logging
from typing import Tuple, List, Dict, Any, Optional

import aiosqlite
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# Constantes de Layout — Grid Matemática Estrita
# ═══════════════════════════════════════════════════════════════════════════

# Canvas principal
CANVAS_W: int = 1920
CANVAS_H: int = 1280
CANVAS_RADIUS: int = 32

# Margens globais e header
PADDING_X: int = 60
PADDING_Y: int = 50
HEADER_H: int = 120

# Grid 3×3
GRID_COLS: int = 3
GRID_ROWS: int = 3
CARDS_PER_PAGE: int = GRID_COLS * GRID_ROWS  # 9

# Dimensões fixas de cada carta na grid (proporção ~3:2)
CARD_W: int = 540
CARD_H: int = 340
CARD_RADIUS: int = 24

# Espaçamento entre cartas
GAP_X: int = 30
GAP_Y: int = 30

# Dimensões internas da carta
AVATAR_SIZE: int = 140          # Quadrado do avatar
AVATAR_RADIUS: int = 20         # Raio dos cantos arredondados do avatar
AVATAR_TOP_MARGIN: int = 20     # Distância do topo do card ao avatar

PILL_H: int = 26                # Altura do pill badge
PILL_RADIUS: int = 13           # Metade da altura para arredondamento perfeito
PILL_TOP_GAP: int = 10          # Gap entre avatar e pill

NAME_TOP_GAP: int = 6           # Gap entre pill e nome

STATS_PANEL_H: int = 72         # Altura do painel de stats
STATS_PANEL_RADIUS: int = 16    # Raio dos cantos do painel
STATS_PANEL_MARGIN_X: int = 16  # Margem horizontal interna do card
STATS_PANEL_BOTTOM_MARGIN: int = 16  # Distância do fundo do card

STAT_BOX_RADIUS: int = 12       # Raio dos cantos de cada sub-bloco de stat
STAT_BOX_GAP: int = 8           # Gap horizontal entre sub-blocos de stat

# ═══════════════════════════════════════════════════════════════════════════
# Paleta de Cores
# ═══════════════════════════════════════════════════════════════════════════

COLOR_BG_DARK: Tuple[int, ...] = (15, 13, 20, 255)
COLOR_CARD_BG: Tuple[int, ...] = (32, 30, 40, 255)
COLOR_CARD_BG_LIGHTER: Tuple[int, ...] = (42, 40, 52, 255)
COLOR_WHITE: Tuple[int, ...] = (255, 255, 255, 255)
COLOR_GRAY: Tuple[int, ...] = (160, 160, 175, 255)
COLOR_DIM: Tuple[int, ...] = (90, 90, 100, 255)
COLOR_GOLD: Tuple[int, ...] = (255, 215, 0, 255)
COLOR_STAT_PANEL: Tuple[int, ...] = (18, 16, 24, 230)
COLOR_STAT_BOX: Tuple[int, ...] = (10, 8, 16, 255)

# Cores por stat
COLOR_ATK: Tuple[int, ...] = (255, 60, 60, 255)
COLOR_DEF: Tuple[int, ...] = (80, 180, 255, 255)
COLOR_SPD: Tuple[int, ...] = (255, 220, 50, 255)

# Mapa de cores por nível de raridade (usado no pill badge e borda sutil)
RARITY_COLORS: Dict[str, Tuple[int, int, int, int]] = {
    "Comum":     (140, 140, 150, 255),
    "Raro":      (60, 130, 255, 255),
    "Épico":     (180, 60, 255, 255),
    "Epico":     (180, 60, 255, 255),
    "Lendário":  (255, 180, 0, 255),
    "Lendario":  (255, 180, 0, 255),
    "Mítico":    (255, 50, 50, 255),
    "Mitico":    (255, 50, 50, 255),
}

# Cor de placeholder para o avatar (gradiente escuro estilizado)
COLOR_AVATAR_PLACEHOLDER_TOP: Tuple[int, ...] = (55, 50, 70, 255)
COLOR_AVATAR_PLACEHOLDER_BOT: Tuple[int, ...] = (30, 28, 38, 255)


# ═══════════════════════════════════════════════════════════════════════════
# Classe Principal
# ═══════════════════════════════════════════════════════════════════════════

class InventoryRenderer:
    """
    Motor de renderização assíncrono para o painel de inventário TCG.

    Conecta ao banco aiosqlite, busca cartas paginadas e gera uma imagem
    PNG de alta qualidade em buffer de bytes, pronta para discord.File.

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
        # Carrega todas as fontes uma única vez no construtor (reutilização)
        self._load_fonts()

    # ──────────────────────────────────────────────────────────────────────
    # Font Management — Instanciação única e reutilizável
    # ──────────────────────────────────────────────────────────────────────

    def _load_fonts(self) -> None:
        """
        Carrega todas as variantes de fonte necessárias em atributos do
        renderer. Fontes são reutilizadas em cada chamada de renderização,
        evitando I/O repetitivo de disco.
        """
        pairs = {
            # Header do canvas
            "font_title":        ("Montserrat-Black.ttf",   48),
            "font_subtitle":     ("Poppins-Regular.ttf",    20),
            "font_page":         ("Montserrat-Black.ttf",   30),
            # Card — nome do personagem
            "font_card_name":    ("Montserrat-Black.ttf",   24),
            # Card — pill de raridade
            "font_card_rarity":  ("Poppins-Bold.ttf",       13),
            # Card — labels de atributo (ATK, DEF, SPD)
            "font_card_label":   ("Poppins-Regular.ttf",    11),
            # Card — valores numéricos de atributo
            "font_card_stat":    ("Montserrat-Black.ttf",   26),
            # Estado vazio
            "font_empty_title":  ("Montserrat-Black.ttf",   36),
            "font_empty_sub":    ("Poppins-Regular.ttf",    18),
        }
        for attr, (filename, size) in pairs.items():
            path = os.path.join(self.fonts_path, filename)
            try:
                setattr(self, attr, ImageFont.truetype(path, size=size))
            except OSError:
                logger.warning("Fonte '%s' não encontrada, usando fallback.", path)
                setattr(self, attr, ImageFont.load_default())

    # ──────────────────────────────────────────────────────────────────────
    # Database Access Layer
    # ──────────────────────────────────────────────────────────────────────

    async def _fetch_inventory_page(
        self, usuario_id: str, pagina: int
    ) -> Tuple[List[Dict[str, Any]], int, int]:
        """
        Busca as cartas da página solicitada e calcula o total de páginas.

        Returns
        -------
        (cards, current_page, total_pages)
        """
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

            # Clamp da página dentro dos limites
            safe_page = max(0, min(pagina, total_pages - 1))
            offset = safe_page * CARDS_PER_PAGE

            # Fetch com JOIN para trazer nome e raridade do template
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

    # ──────────────────────────────────────────────────────────────────────
    # Primitivas Gráficas Reutilizáveis
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _rounded_rect_mask(size: Tuple[int, int], radius: int) -> Image.Image:
        """
        Gera uma máscara L (grayscale) com retângulo arredondado usando
        supersampling 4× para anti-aliasing de alta fidelidade.
        """
        ss = 4
        big = (size[0] * ss, size[1] * ss)
        mask = Image.new("L", big, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, big[0] - 1, big[1] - 1), radius=radius * ss, fill=255,
        )
        return mask.resize(size, Image.Resampling.LANCZOS)

    @staticmethod
    def _center_text(
        draw: ImageDraw.ImageDraw,
        box: Tuple[int, int, int, int],
        text: str,
        font: ImageFont.FreeTypeFont,
        fill: Tuple[int, ...],
    ) -> None:
        """
        Centraliza um texto horizontal e verticalmente dentro de uma
        bounding box usando cálculo preciso via font.getbbox().

        Parameters
        ----------
        box : (x0, y0, x1, y1) da região de contenção.
        """
        left, top, right, bottom = font.getbbox(text)
        tw = right - left
        th = bottom - top

        bw = box[2] - box[0]
        bh = box[3] - box[1]

        tx = box[0] + (bw - tw) / 2 - left
        ty = box[1] + (bh - th) / 2 - top
        draw.text((tx, ty), text, font=font, fill=fill)

    # ──────────────────────────────────────────────────────────────────────
    # Componentes Individuais da Carta
    # ──────────────────────────────────────────────────────────────────────

    def _draw_card_background(
        self,
        card_canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
        rarity_color: Tuple[int, int, int, int],
    ) -> None:
        """
        Renderiza o fundo escuro do card com gradiente vertical sutil,
        borda arredondada e outline tênue na cor da raridade.
        """
        cw, ch = card_canvas.size

        # Gradiente vertical escuro (topo ligeiramente mais claro)
        for y in range(ch):
            t = y / max(ch - 1, 1)
            r = int(COLOR_CARD_BG_LIGHTER[0] + (COLOR_CARD_BG[0] - COLOR_CARD_BG_LIGHTER[0]) * t)
            g = int(COLOR_CARD_BG_LIGHTER[1] + (COLOR_CARD_BG[1] - COLOR_CARD_BG_LIGHTER[1]) * t)
            b = int(COLOR_CARD_BG_LIGHTER[2] + (COLOR_CARD_BG[2] - COLOR_CARD_BG_LIGHTER[2]) * t)
            draw.line([(0, y), (cw, y)], fill=(r, g, b, 255))

        # Borda sutil na cor da raridade
        draw.rounded_rectangle(
            (0, 0, cw - 1, ch - 1),
            radius=CARD_RADIUS,
            outline=(*rarity_color[:3], 60),
            width=2,
        )

    def _draw_card_avatar(
        self,
        card_canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
        processed_avatar: Optional[Image.Image] = None,
    ) -> int:
        """
        Renderiza o avatar centralizado no topo do card. Retorna a coordenada Y inferior.

        Se `processed_avatar` for fornecido (já recortado e mascarado no escopo pai),
        faz o paste direto. Caso contrário, gera um gradiente escuro elegante com
        ícone de silhueta como fallback resiliente.
        """
        cw = card_canvas.size[0]
        ax = (cw - AVATAR_SIZE) // 2
        ay = AVATAR_TOP_MARGIN

        if processed_avatar:
            card_canvas.alpha_composite(processed_avatar, (ax, ay))
            return ay + AVATAR_SIZE

        # Fallback de Falha: Placeholder com gradiente e silhueta
        avatar_img = Image.new("RGBA", (AVATAR_SIZE, AVATAR_SIZE), (0, 0, 0, 0))
        av_draw = ImageDraw.Draw(avatar_img)

        for y_line in range(AVATAR_SIZE):
            t = y_line / max(AVATAR_SIZE - 1, 1)
            r = int(COLOR_AVATAR_PLACEHOLDER_TOP[0] + (COLOR_AVATAR_PLACEHOLDER_BOT[0] - COLOR_AVATAR_PLACEHOLDER_TOP[0]) * t)
            g = int(COLOR_AVATAR_PLACEHOLDER_TOP[1] + (COLOR_AVATAR_PLACEHOLDER_BOT[1] - COLOR_AVATAR_PLACEHOLDER_TOP[1]) * t)
            b = int(COLOR_AVATAR_PLACEHOLDER_TOP[2] + (COLOR_AVATAR_PLACEHOLDER_BOT[2] - COLOR_AVATAR_PLACEHOLDER_TOP[2]) * t)
            av_draw.line([(0, y_line), (AVATAR_SIZE, y_line)], fill=(r, g, b, 255))

        # Ícone silhueta minimalista
        cx_s = AVATAR_SIZE // 2
        cy_head = AVATAR_SIZE // 3
        head_r = AVATAR_SIZE // 7
        av_draw.ellipse(
            (cx_s - head_r, cy_head - head_r, cx_s + head_r, cy_head + head_r),
            fill=(80, 75, 95, 180),
        )
        body_top = cy_head + head_r + 4
        body_w = AVATAR_SIZE // 3
        av_draw.rounded_rectangle(
            (cx_s - body_w, body_top, cx_s + body_w, AVATAR_SIZE - 4),
            radius=body_w // 2,
            fill=(80, 75, 95, 150),
        )

        # Aplica máscara de cantos arredondados no fallback
        avatar_mask = self._rounded_rect_mask((AVATAR_SIZE, AVATAR_SIZE), AVATAR_RADIUS)
        masked_avatar = Image.new("RGBA", (AVATAR_SIZE, AVATAR_SIZE), (0, 0, 0, 0))
        masked_avatar.paste(avatar_img, (0, 0), avatar_mask)

        # Borda fina
        av_border_draw = ImageDraw.Draw(masked_avatar)
        av_border_draw.rounded_rectangle(
            (0, 0, AVATAR_SIZE - 1, AVATAR_SIZE - 1),
            radius=AVATAR_RADIUS,
            outline=(255, 255, 255, 40),
            width=2,
        )

        card_canvas.alpha_composite(masked_avatar, (ax, ay))

        avatar_img.close()
        masked_avatar.close()

        return ay + AVATAR_SIZE

    def _draw_rarity_badge(
        self,
        card_canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
        raridade: str,
        rarity_color: Tuple[int, int, int, int],
        y_start: int,
    ) -> int:
        """
        Renderiza o pill badge de raridade centralizado horizontalmente
        logo abaixo do avatar. Retorna a coordenada Y inferior da pill.
        """
        cw = card_canvas.size[0]
        rar_text = raridade.upper()

        # Mede o texto para calcular a largura da pill
        left, top, right, bottom = self.font_card_rarity.getbbox(rar_text)
        text_w = right - left
        pill_w = text_w + 28  # padding horizontal

        pill_x = (cw - pill_w) // 2
        pill_y = y_start + PILL_TOP_GAP
        pill_box = (pill_x, pill_y, pill_x + pill_w, pill_y + PILL_H)

        # Background da pill com cor atenuada da raridade
        pill_fill = (
            rarity_color[0] // 4,
            rarity_color[1] // 4,
            rarity_color[2] // 4,
            220,
        )
        draw.rounded_rectangle(pill_box, radius=PILL_RADIUS, fill=pill_fill)
        draw.rounded_rectangle(
            pill_box, radius=PILL_RADIUS,
            outline=(*rarity_color[:3], 80), width=1,
        )

        # Texto centralizado na pill
        self._center_text(draw, pill_box, rar_text, self.font_card_rarity, COLOR_WHITE)

        return pill_y + PILL_H

    def _draw_card_name(
        self,
        draw: ImageDraw.ImageDraw,
        cw: int,
        nome: str,
        y_start: int,
    ) -> int:
        """
        Renderiza o nome do personagem em caixa alta, centralizado
        horizontalmente. Trunca nomes longos com '…'. Retorna Y inferior.
        """
        name_text = nome.upper()
        if len(name_text) > 16:
            name_text = name_text[:15] + "…"

        name_y = y_start + NAME_TOP_GAP
        left, top, right, bottom = self.font_card_name.getbbox(name_text)
        text_h = bottom - top

        name_box = (0, name_y, cw, name_y + text_h + 4)
        self._center_text(draw, name_box, name_text, self.font_card_name, COLOR_WHITE)

        return name_y + text_h + 4

    def _draw_stats_panel(
        self,
        card_canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
        atk: int,
        defesa: int,
        spd: int,
    ) -> None:
        """
        Renderiza o container de atributos ancorado ao fundo do card.
        Contém 3 sub-blocos proporcionais (ATK, DEF, SPD) dispostos em
        colunas horizontais, cada um com label acima e valor abaixo.
        """
        cw, ch = card_canvas.size

        # Posicionamento do painel principal (ancorado ao fundo)
        panel_x = STATS_PANEL_MARGIN_X
        panel_w = cw - (STATS_PANEL_MARGIN_X * 2)
        panel_y = ch - STATS_PANEL_BOTTOM_MARGIN - STATS_PANEL_H
        panel_box = (panel_x, panel_y, panel_x + panel_w, panel_y + STATS_PANEL_H)

        # Background do painel
        draw.rounded_rectangle(
            panel_box, radius=STATS_PANEL_RADIUS,
            fill=COLOR_STAT_PANEL,
        )
        draw.rounded_rectangle(
            panel_box, radius=STATS_PANEL_RADIUS,
            outline=(255, 255, 255, 20), width=1,
        )

        # Divisão em 3 sub-blocos proporcionais
        inner_margin = 8
        total_gap = STAT_BOX_GAP * 2
        box_w = (panel_w - (inner_margin * 2) - total_gap) // 3
        box_h = STATS_PANEL_H - (inner_margin * 2)
        box_y = panel_y + inner_margin

        stats = [
            ("ATK", str(atk), COLOR_ATK),
            ("DEF", str(defesa), COLOR_DEF),
            ("SPD", str(spd), COLOR_SPD),
        ]

        for i, (label, val, color) in enumerate(stats):
            bx = panel_x + inner_margin + i * (box_w + STAT_BOX_GAP)
            stat_box = (bx, box_y, bx + box_w, box_y + box_h)

            # Background do sub-bloco
            draw.rounded_rectangle(
                stat_box, radius=STAT_BOX_RADIUS,
                fill=COLOR_STAT_BOX,
            )

            # Label (ATK/DEF/SPD) — terço superior
            label_h = box_h // 3
            label_box = (bx, box_y, bx + box_w, box_y + label_h)
            self._center_text(draw, label_box, label, self.font_card_label, COLOR_GRAY)

            # Separador horizontal fino
            sep_y = box_y + label_h
            draw.line(
                [(bx + 6, sep_y), (bx + box_w - 6, sep_y)],
                fill=(255, 255, 255, 30), width=1,
            )

            # Valor numérico — dois terços inferiores
            val_box = (bx, sep_y, bx + box_w, box_y + box_h)
            self._center_text(draw, val_box, val, self.font_card_stat, color)

    # ──────────────────────────────────────────────────────────────────────
    # Composição de Mini-Carta Individual
    # ──────────────────────────────────────────────────────────────────────

    def _render_card(self, card: Dict[str, Any]) -> Image.Image:
        """
        Compõe uma carta completa em um canvas RGBA isolado, combinando
        todos os componentes (background, avatar, badge, nome, stats).

        Parameters
        ----------
        card : dict
            Dicionário com keys: nome, raridade, atk, defesa, spd, uuid, passiva.

        Returns
        -------
        Image.Image
            Canvas RGBA da carta com máscara arredondada aplicada.
        """
        nome = card.get("nome", "???")
        raridade = card.get("raridade", "Comum")
        atk = card.get("atk", 0)
        defesa = card.get("defesa", 0)
        spd = card.get("spd", 0)
        rarity_color = RARITY_COLORS.get(raridade, RARITY_COLORS["Comum"])

        # Canvas isolado para a carta
        canvas = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)

        # 1. Background com gradiente e borda
        self._draw_card_background(canvas, draw, rarity_color)

        # 2. Avatar (Imagem real ou placeholder)
        avatar_bytes = card.get("avatar_bytes")
        avatar_bottom = self._draw_card_avatar(canvas, draw, avatar_bytes)

        # 3. Pill badge de raridade
        badge_bottom = self._draw_rarity_badge(
            canvas, draw, raridade, rarity_color, avatar_bottom,
        )

        # 4. Nome do personagem
        self._draw_card_name(draw, CARD_W, nome, badge_bottom)

        # 5. Painel de stats (ancorado ao fundo)
        self._draw_stats_panel(canvas, draw, atk, defesa, spd)

        # Aplicar máscara de cantos arredondados ao card inteiro
        card_mask = self._rounded_rect_mask((CARD_W, CARD_H), CARD_RADIUS)
        output = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))
        output.paste(canvas, (0, 0), card_mask)

        canvas.close()
        return output

    # ──────────────────────────────────────────────────────────────────────
    # Canvas Principal — Composição com Header + Grid
    # ──────────────────────────────────────────────────────────────────────

    def _render_canvas(
        self,
        cards: List[Dict[str, Any]],
        usuario_id: str,
        current_page: int,
        total_pages: int,
    ) -> Image.Image:
        """
        Compõe o canvas completo: header informativo + grade 3×3 de cartas.

        O posicionamento de cada carta é calculado estritamente via índice:
            col = idx % 3
            row = idx // 3
        garantindo alinhamento perfeito sem distorção.
        """
        canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), COLOR_BG_DARK)
        draw = ImageDraw.Draw(canvas)

        # ── Borda sutil do canvas ──
        draw.rounded_rectangle(
            (0, 0, CANVAS_W - 1, CANVAS_H - 1),
            radius=CANVAS_RADIUS,
            outline=(255, 255, 255, 20),
            width=2,
        )

        # ── Header ──
        # Título
        draw.text(
            (PADDING_X, PADDING_Y),
            "INVENTÁRIO // COLEÇÃO",
            font=self.font_title, fill=COLOR_WHITE,
        )
        # Subtítulo com ID do jogador
        draw.text(
            (PADDING_X, PADDING_Y + 55),
            f"ID_RESONANCE // {usuario_id}",
            font=self.font_subtitle, fill=COLOR_GRAY,
        )
        # Paginação alinhada à direita
        page_text = f"[ PÁGINA {current_page + 1} / {total_pages} ]"
        pl, pt, pr, pb = self.font_page.getbbox(page_text)
        page_x = CANVAS_W - PADDING_X - (pr - pl)
        draw.text(
            (page_x, PADDING_Y + 10),
            page_text,
            font=self.font_page, fill=COLOR_GOLD,
        )

        # Linha separadora do header
        sep_y = PADDING_Y + HEADER_H
        draw.line(
            [(PADDING_X, sep_y), (CANVAS_W - PADDING_X, sep_y)],
            fill=(255, 255, 255, 25), width=1,
        )

        # ── Grid 3×3 ──
        # Calcula a origem da grid para centralizar horizontalmente
        grid_total_w = (CARD_W * GRID_COLS) + (GAP_X * (GRID_COLS - 1))
        grid_origin_x = (CANVAS_W - grid_total_w) // 2
        grid_origin_y = sep_y + 30  # Margem abaixo do separador

        for idx, card in enumerate(cards):
            col = idx % GRID_COLS
            row = idx // GRID_COLS

            # Posição exata do slot
            cell_x = grid_origin_x + col * (CARD_W + GAP_X)
            cell_y = grid_origin_y + row * (CARD_H + GAP_Y)

            # Renderiza a carta individual
            card_img = self._render_card(card)

            # Drop shadow sutil abaixo de cada carta
            shadow = Image.new("RGBA", (CARD_W + 16, CARD_H + 16), (0, 0, 0, 0))
            sd = ImageDraw.Draw(shadow)
            sd.rounded_rectangle(
                (8, 10, CARD_W + 8, CARD_H + 10),
                radius=CARD_RADIUS,
                fill=(0, 0, 0, 80),
            )
            shadow = shadow.filter(ImageFilter.GaussianBlur(10))
            canvas.alpha_composite(shadow, (cell_x - 8, cell_y - 5))

            # Cola a carta na posição calculada
            canvas.alpha_composite(card_img, (cell_x, cell_y))

            # Cleanup imediato
            card_img.close()
            shadow.close()

        return canvas

    # ──────────────────────────────────────────────────────────────────────
    # Estado Vazio (inventário sem cartas)
    # ──────────────────────────────────────────────────────────────────────

    def _render_empty_state(self, usuario_id: str) -> Image.Image:
        """
        Renderiza o estado vazio estilizado quando o inventário não possui
        cartas — exibe mensagem centralizada e ID do jogador.
        """
        canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), COLOR_BG_DARK)
        draw = ImageDraw.Draw(canvas)

        # Borda
        draw.rounded_rectangle(
            (0, 0, CANVAS_W - 1, CANVAS_H - 1),
            radius=CANVAS_RADIUS,
            outline=(255, 255, 255, 20),
            width=2,
        )

        # Mensagem central
        msg = "NENHUM ATIVO DETECTADO NA FORJA"
        box = (0, 0, CANVAS_W, CANVAS_H - 40)
        self._center_text(draw, box, msg, self.font_empty_title, COLOR_DIM)

        # Sub-mensagem com ID
        sub = f"ID // {usuario_id}"
        sl, st, sr, sb = self.font_empty_sub.getbbox(sub)
        sub_x = (CANVAS_W - (sr - sl)) // 2
        draw.text(
            (sub_x, CANVAS_H // 2 + 40),
            sub, font=self.font_empty_sub, fill=COLOR_GRAY,
        )

        return canvas

    # ──────────────────────────────────────────────────────────────────────
    # Ponto de Entrada Público
    # ──────────────────────────────────────────────────────────────────────

    async def _fetch_avatar_bytes(self, session, card: Dict[str, Any]):
        """Helper para baixar bytes do avatar e armazenar no dict da carta."""
        url = card.get("avatar_url")
        if not url:
            return
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    card["avatar_bytes"] = await resp.read()
        except Exception as e:
            logger.warning(f"Falha ao baixar avatar {url}: {e}")

    async def gerar_imagem_inventario(
        self,
        usuario_id: str,
        pagina: int = 0,
    ) -> io.BytesIO:
        """
        Pipeline completo: DB → Download PFPs → Renderização → Buffer PNG.

        Parameters
        ----------
        usuario_id : str
            ID Discord do jogador.
        pagina : int
            Número da página (0-indexed).

        Returns
        -------
        io.BytesIO
            Buffer PNG pronto para envio via discord.File.
        """
        # 1. Fetch do banco de dados
        cards, current_page, total_pages = await self._fetch_inventory_page(
            usuario_id, pagina,
        )

        # 2. Download assíncrono dos avatares para evitar block de CPU
        if cards:
            import aiohttp
            import asyncio
            timeout = aiohttp.ClientTimeout(total=4.0)
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    tasks = [self._fetch_avatar_bytes(session, card) for card in cards if card.get("avatar_url")]
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as e:
                logger.error(f"Erro no pool de conexões ao baixar avatares: {e}")

        # 3. Renderização (CPU-bound)
        if not cards:
            img = self._render_empty_state(str(usuario_id))
        else:
            img = self._render_canvas(
                cards, str(usuario_id), current_page, total_pages,
            )

        # 3. Aplicar máscara arredondada final ao canvas
        final_mask = self._rounded_rect_mask((CANVAS_W, CANVAS_H), CANVAS_RADIUS)
        output = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
        output.paste(img, (0, 0), final_mask)

        # 4. Exportar para buffer de bytes
        buffer = io.BytesIO()
        output.save(buffer, format="PNG", compress_level=6)
        buffer.seek(0)

        # Cleanup
        img.close()
        output.close()

        return buffer


# ═══════════════════════════════════════════════════════════════════════════
# Função Autônoma (compatível com a assinatura usada no cog)
# ═══════════════════════════════════════════════════════════════════════════

async def gerar_imagem_inventario(
    db_path: str,
    usuario_id: str,
    pagina: int,
) -> io.BytesIO:
    """
    Wrapper top-level que instancia o renderer e executa a renderização.
    Assinatura exata consumida por tcg_cog.py (linhas 138 e 301).
    """
    renderer = InventoryRenderer(db_path=db_path)
    return await renderer.gerar_imagem_inventario(usuario_id, pagina)
