from __future__ import annotations

import asyncio
import io
import math
import pathlib
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import OrderedDict as OrderedDictType
from urllib.parse import urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont, ImageOps


# Evita que imagens absurdamente gigantes tentem explodir a memória do bot.
Image.MAX_IMAGE_PIXELS = 25_000_000


# ============================================================
# MODELOS DE DADOS
# ============================================================

@dataclass
class TierItem:
    """
    Representa um item da Tier List.

    name:
        Nome/legenda do item.

    image_url:
        URL opcional enviada pelo usuário.

    image_bytes:
        Bytes baixados da imagem.
        Esse campo é preenchido apenas na hora de gerar a imagem final.
        Se for None, o renderer usa fallback de texto.
    """

    name: str
    image_url: str | None = None
    image_bytes: bytes | None = None


@dataclass
class TierListSession:
    """
    Estado temporário de uma Tier List em criação.
    Fica em RAM e é apagado ao gerar, cancelar ou expirar.
    """

    owner_id: int
    title: str
    tiers: list[str] = field(default_factory=lambda: ["S", "A", "B", "C", "D"])
    items: OrderedDictType[str, list[TierItem]] = field(
        default_factory=lambda: OrderedDict(
            {
                "S": [],
                "A": [],
                "B": [],
                "C": [],
                "D": [],
            }
        )
    )
    panel_message: discord.Message | None = None


# ============================================================
# PARTE 1 — PILLOW RENDERER
# ============================================================

class TierListRenderer:
    """
    Classe responsável APENAS pela imagem.

    Ela não depende de Discord, Interaction, View ou Modal.
    Assim o desenho fica testável e isolado.
    """

    WIDTH = 1200

    # Layout geral
    OUTER_PADDING = 28
    TITLE_HEIGHT = 110
    TIER_LABEL_WIDTH = 150
    TIER_GAP = 8

    # Área interna de cada tier
    ROW_PADDING_X = 18
    ROW_PADDING_Y = 16

    # Cards
    ITEM_WIDTH = 170
    TEXT_ITEM_HEIGHT = 62
    IMAGE_ITEM_HEIGHT = 150
    IMAGE_SIZE = 120

    ITEM_GAP_X = 12
    ITEM_GAP_Y = 12

    MIN_ROW_HEIGHT = 96

    # Cores
    BACKGROUND = "#111116"
    ROW_BACKGROUND = "#202026"
    CARD_BACKGROUND = "#303039"
    CARD_OUTLINE = "#454552"
    TEXT = "#FFFFFF"
    MUTED_TEXT = "#A7A7B3"

    TIER_COLORS = [
        "#FF4D4D",  # S
        "#FF9F43",  # A
        "#FFD93D",  # B
        "#6BCB77",  # C
        "#4D96FF",  # D
        "#9B5DE5",
        "#F15BB5",
        "#00BBF9",
        "#00F5D4",
        "#C77DFF",
    ]

    def __init__(self, font_path: str | None = None) -> None:
        self.font_path = font_path

    def generate_tierlist_image(
        self,
        title: str,
        tiers_dict: OrderedDictType[str, list[TierItem]],
    ) -> io.BytesIO:
        """
        Gera a imagem final da Tier List.

        tiers_dict:
            OrderedDict({
                "S": [TierItem(...), TierItem(...)],
                "A": [TierItem(...)],
            })
        """

        measuring_img = Image.new("RGB", (self.WIDTH, 10), self.BACKGROUND)
        draw = ImageDraw.Draw(measuring_img)

        title_font = self._load_font(44, bold=True)
        tier_font = self._load_font(42, bold=True)
        item_font = self._load_font(24, bold=True)
        empty_font = self._load_font(22, bold=False)

        # ------------------------------------------------------------
        # LARGURA DISPONÍVEL PARA OS CARDS
        # ------------------------------------------------------------
        #
        # A imagem tem largura fixa de 1200px.
        #
        # Dentro dela, removemos:
        # - padding externo esquerdo e direito;
        # - largura da coluna colorida da tier;
        # - padding interno da área dos cards.
        #
        # O resultado é a área onde os cards podem existir.
        # Nenhum card pode passar desse limite.
        # ------------------------------------------------------------

        items_area_width = (
            self.WIDTH
            - self.OUTER_PADDING * 2
            - self.TIER_LABEL_WIDTH
            - self.ROW_PADDING_X * 2
        )

        # ------------------------------------------------------------
        # QUANTOS CARDS CABEM POR LINHA
        # ------------------------------------------------------------
        #
        # Fórmula:
        #
        #   floor((largura_disponivel + gap) / (largura_card + gap))
        #
        # O +gap no numerador existe porque em uma linha com N cards
        # existem apenas N-1 espaços entre cards.
        #
        # Exemplo:
        # - área = 958
        # - card = 170
        # - gap = 12
        #
        # floor((958 + 12) / (170 + 12)) = floor(970 / 182) = 5
        #
        # 5 cards:
        # 5 * 170 + 4 * 12 = 898px
        #
        # 6 cards:
        # 6 * 170 + 5 * 12 = 1080px
        #
        # Então 6 estouraria a imagem.
        # ------------------------------------------------------------

        items_per_line = max(
            1,
            math.floor((items_area_width + self.ITEM_GAP_X) / (self.ITEM_WIDTH + self.ITEM_GAP_X)),
        )

        row_layouts: list[dict] = []

        for index, (tier_name, items) in enumerate(tiers_dict.items()):
            item_count = len(items)
            line_count = max(1, math.ceil(item_count / items_per_line))

            # ------------------------------------------------------------
            # ALTURA DINÂMICA COM ITENS MISTOS
            # ------------------------------------------------------------
            #
            # Agora existem dois tipos de cards:
            #
            # 1. Card de texto:
            #    altura = TEXT_ITEM_HEIGHT
            #
            # 2. Card com imagem:
            #    altura = IMAGE_ITEM_HEIGHT
            #
            # Não dá mais para calcular a altura assim:
            #
            #   line_count * ITEM_HEIGHT
            #
            # porque cada linha pode ter uma mistura diferente.
            #
            # Exemplo:
            #
            # Linha 0: imagem, texto, texto, imagem, texto
            # A altura dessa linha precisa ser 150px.
            #
            # Linha 1: texto, texto, texto
            # A altura dessa linha precisa ser só 62px.
            #
            # Por isso calculamos a altura de CADA linha separadamente:
            #
            #   line_heights = [62, 62, 62...]
            #
            # Para cada item:
            #
            #   item_row = item_index // items_per_line
            #
            # Se o item tiver imagem, a linha dele precisa ter pelo menos 150px.
            # Se for texto, pelo menos 62px.
            #
            # No final, a altura da tier é:
            #
            #   soma(line_heights)
            #   + gaps verticais entre linhas
            #   + padding vertical
            #
            # Isso impede:
            # - imagem invadindo a próxima tier;
            # - card saindo para fora da direita;
            # - fileira ficando alta demais sem necessidade.
            # ------------------------------------------------------------

            line_heights = [self.TEXT_ITEM_HEIGHT for _ in range(line_count)]

            for item_index, item in enumerate(items):
                item_row = item_index // items_per_line

                item_height = (
                    self.IMAGE_ITEM_HEIGHT
                    if item.image_bytes
                    else self.TEXT_ITEM_HEIGHT
                )

                line_heights[item_row] = max(line_heights[item_row], item_height)

            content_height = (
                sum(line_heights)
                + max(0, line_count - 1) * self.ITEM_GAP_Y
                + self.ROW_PADDING_Y * 2
            )

            row_height = max(self.MIN_ROW_HEIGHT, content_height)

            row_layouts.append(
                {
                    "tier": tier_name,
                    "items": items,
                    "color": self.TIER_COLORS[index % len(self.TIER_COLORS)],
                    "line_count": line_count,
                    "row_height": row_height,
                    "items_per_line": items_per_line,
                    "line_heights": line_heights,
                }
            )

        total_rows_height = sum(row["row_height"] for row in row_layouts)
        total_gaps_height = max(0, len(row_layouts) - 1) * self.TIER_GAP

        image_height = (
            self.TITLE_HEIGHT
            + self.OUTER_PADDING
            + total_rows_height
            + total_gaps_height
            + self.OUTER_PADDING
        )

        image = Image.new("RGB", (self.WIDTH, image_height), self.BACKGROUND)
        draw = ImageDraw.Draw(image)

        self._draw_centered_text(
            draw=draw,
            box=(self.OUTER_PADDING, 0, self.WIDTH - self.OUTER_PADDING, self.TITLE_HEIGHT),
            text=title,
            font=title_font,
            fill=self.TEXT,
            max_width=self.WIDTH - self.OUTER_PADDING * 2,
            bold=True,
        )

        y = self.TITLE_HEIGHT + self.OUTER_PADDING

        for row in row_layouts:
            tier_name = row["tier"]
            items = row["items"]
            color = row["color"]
            row_height = row["row_height"]
            items_per_line = row["items_per_line"]
            line_heights = row["line_heights"]

            row_x1 = self.OUTER_PADDING
            row_y1 = y
            row_x2 = self.WIDTH - self.OUTER_PADDING
            row_y2 = y + row_height

            draw.rounded_rectangle(
                (row_x1, row_y1, row_x2, row_y2),
                radius=18,
                fill=self.ROW_BACKGROUND,
            )

            label_x1 = row_x1
            label_y1 = row_y1
            label_x2 = row_x1 + self.TIER_LABEL_WIDTH
            label_y2 = row_y2

            draw.rounded_rectangle(
                (label_x1, label_y1, label_x2, label_y2),
                radius=18,
                fill=color,
            )

            # Remove o arredondamento da direita do bloco da tier.
            draw.rectangle(
                (label_x2 - 18, label_y1, label_x2, label_y2),
                fill=color,
            )

            self._draw_centered_text(
                draw=draw,
                box=(label_x1, label_y1, label_x2, label_y2),
                text=tier_name,
                font=tier_font,
                fill="#111116",
                max_width=self.TIER_LABEL_WIDTH - 18,
                bold=True,
            )

            items_start_x = label_x2 + self.ROW_PADDING_X
            items_start_y = row_y1 + self.ROW_PADDING_Y

            if not items:
                self._draw_centered_text(
                    draw=draw,
                    box=(items_start_x, row_y1, row_x2 - self.ROW_PADDING_X, row_y2),
                    text="Sem itens ainda",
                    font=empty_font,
                    fill=self.MUTED_TEXT,
                    max_width=row_x2 - items_start_x - self.ROW_PADDING_X,
                    bold=False,
                )
            else:
                for item_index, item in enumerate(items):
                    item_row = item_index // items_per_line
                    item_col = item_index % items_per_line

                    # ------------------------------------------------------------
                    # Y COM LINHAS DE ALTURA VARIÁVEL
                    # ------------------------------------------------------------
                    #
                    # Antes, com cards de altura fixa, dava para fazer:
                    #
                    #   y = start_y + row_index * (ITEM_HEIGHT + GAP)
                    #
                    # Agora isso quebra, porque uma linha com imagem tem 150px
                    # e uma linha só com texto tem 62px.
                    #
                    # Então o Y do item precisa ser:
                    #
                    #   soma das alturas das linhas anteriores
                    #   + gaps anteriores
                    #
                    # Exemplo:
                    #
                    # line_heights = [150, 62, 150]
                    #
                    # Item na linha 0:
                    # offset_y = 0
                    #
                    # Item na linha 1:
                    # offset_y = 150 + 1 gap
                    #
                    # Item na linha 2:
                    # offset_y = 150 + 62 + 2 gaps
                    #
                    # Isso faz a tier se expandir naturalmente,
                    # sem atropelar a fileira de baixo.
                    # ------------------------------------------------------------

                    offset_y = sum(line_heights[:item_row]) + item_row * self.ITEM_GAP_Y

                    item_height = (
                        self.IMAGE_ITEM_HEIGHT
                        if item.image_bytes
                        else self.TEXT_ITEM_HEIGHT
                    )

                    item_x1 = items_start_x + item_col * (self.ITEM_WIDTH + self.ITEM_GAP_X)
                    item_y1 = items_start_y + offset_y
                    item_x2 = item_x1 + self.ITEM_WIDTH
                    item_y2 = item_y1 + item_height

                    draw.rounded_rectangle(
                        (item_x1, item_y1, item_x2, item_y2),
                        radius=14,
                        fill=self.CARD_BACKGROUND,
                        outline=self.CARD_OUTLINE,
                        width=2,
                    )

                    if item.image_bytes:
                        self._draw_image_item(
                            base_image=image,
                            draw=draw,
                            item=item,
                            box=(item_x1, item_y1, item_x2, item_y2),
                            font=item_font,
                        )
                    else:
                        self._draw_centered_wrapped_text(
                            draw=draw,
                            box=(item_x1 + 10, item_y1 + 6, item_x2 - 10, item_y2 - 6),
                            text=item.name,
                            font=item_font,
                            fill=self.TEXT,
                        )

            y += row_height + self.TIER_GAP

        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        buffer.seek(0)
        return buffer

    def _draw_image_item(
        self,
        base_image: Image.Image,
        draw: ImageDraw.ImageDraw,
        item: TierItem,
        box: tuple[int, int, int, int],
        font: ImageFont.ImageFont,
    ) -> None:
        """
        Desenha um item com imagem.

        A imagem original pode vir em qualquer proporção:
        - quadrada;
        - vertical;
        - 16:9;
        - gigante;
        - minúscula.

        ImageOps.fit cria uma versão padronizada:
        - tamanho fixo;
        - crop centralizado;
        - sem distorção;
        - sem esticar a imagem.
        """

        x1, y1, x2, y2 = box

        try:
            if not item.image_bytes:
                raise ValueError("Item sem bytes de imagem.")

            with Image.open(io.BytesIO(item.image_bytes)) as raw:
                raw = raw.convert("RGB")

                fitted = ImageOps.fit(
                    raw,
                    (self.IMAGE_SIZE, self.IMAGE_SIZE),
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )

        except Exception:
            # Fallback se a URL baixou algo corrompido ou inválido.
            self._draw_centered_wrapped_text(
                draw=draw,
                box=(x1 + 10, y1 + 6, x2 - 10, y2 - 6),
                text=item.name,
                font=font,
                fill=self.TEXT,
            )
            return

        image_x = x1 + (self.ITEM_WIDTH - self.IMAGE_SIZE) // 2
        image_y = y1 + 12

        # Máscara arredondada para a imagem ficar com estética de card.
        mask = Image.new("L", (self.IMAGE_SIZE, self.IMAGE_SIZE), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle(
            (0, 0, self.IMAGE_SIZE, self.IMAGE_SIZE),
            radius=12,
            fill=255,
        )

        base_image.paste(
            fitted,
            (image_x, image_y),
            mask,
        )

        # Tarja escura na base da imagem para a legenda.
        caption_height = 30
        caption_y1 = image_y + self.IMAGE_SIZE - caption_height
        caption_y2 = image_y + self.IMAGE_SIZE

        overlay = Image.new("RGBA", (self.IMAGE_SIZE, caption_height), (0, 0, 0, 175))
        base_image.paste(
            overlay,
            (image_x, caption_y1),
            overlay,
        )

        caption_font = self._load_font(16, bold=True)

        self._draw_centered_text(
            draw=draw,
            box=(image_x + 4, caption_y1, image_x + self.IMAGE_SIZE - 4, caption_y2),
            text=item.name,
            font=caption_font,
            fill=self.TEXT,
            max_width=self.IMAGE_SIZE - 8,
            bold=True,
        )

    def _load_font(
        self,
        size: int,
        *,
        bold: bool = False,
    ) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        candidates: list[str] = []

        if self.font_path:
            candidates.append(self.font_path)

        assets_fonts = pathlib.Path("assets/fonts")
        if assets_fonts.exists():
            candidates.extend(str(path) for path in assets_fonts.glob("*.ttf"))

        if bold:
            candidates.extend(
                [
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    "C:/Windows/Fonts/arialbd.ttf",
                    "arialbd.ttf",
                ]
            )
        else:
            candidates.extend(
                [
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                    "C:/Windows/Fonts/arial.ttf",
                    "arial.ttf",
                ]
            )

        for candidate in candidates:
            try:
                return ImageFont.truetype(candidate, size=size)
            except Exception:
                continue

        return ImageFont.load_default()

    def _draw_centered_text(
        self,
        draw: ImageDraw.ImageDraw,
        box: tuple[int, int, int, int],
        text: str,
        font: ImageFont.ImageFont,
        fill: str,
        max_width: int,
        *,
        bold: bool,
    ) -> None:
        x1, y1, x2, y2 = box
        current_font = font

        if isinstance(font, ImageFont.FreeTypeFont):
            size = font.size

            while size > 10:
                bbox = draw.textbbox((0, 0), text, font=current_font)
                text_width = bbox[2] - bbox[0]

                if text_width <= max_width:
                    break

                size -= 2
                current_font = self._load_font(size, bold=bold)

        bbox = draw.textbbox((0, 0), text, font=current_font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        text_x = x1 + ((x2 - x1) - text_width) / 2
        text_y = y1 + ((y2 - y1) - text_height) / 2 - bbox[1]

        draw.text((text_x, text_y), text, font=current_font, fill=fill)

    def _draw_centered_wrapped_text(
        self,
        draw: ImageDraw.ImageDraw,
        box: tuple[int, int, int, int],
        text: str,
        font: ImageFont.ImageFont,
        fill: str,
    ) -> None:
        x1, y1, x2, y2 = box
        max_width = x2 - x1
        max_height = y2 - y1

        current_font = font
        lines = [text]

        if isinstance(font, ImageFont.FreeTypeFont):
            size = font.size

            while size >= 12:
                current_font = self._load_font(size, bold=True)
                lines = self._wrap_text_by_pixels(
                    draw=draw,
                    text=text,
                    font=current_font,
                    max_width=max_width,
                    max_lines=2,
                )

                line_height = self._line_height(draw, current_font)
                total_height = len(lines) * line_height + max(0, len(lines) - 1) * 3

                widest_line = max(
                    (
                        draw.textbbox((0, 0), line, font=current_font)[2]
                        - draw.textbbox((0, 0), line, font=current_font)[0]
                        for line in lines
                    ),
                    default=0,
                )

                if widest_line <= max_width and total_height <= max_height:
                    break

                size -= 2

        line_height = self._line_height(draw, current_font)
        total_text_height = len(lines) * line_height + max(0, len(lines) - 1) * 3

        current_y = y1 + (max_height - total_text_height) / 2

        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=current_font)
            line_width = bbox[2] - bbox[0]

            line_x = x1 + (max_width - line_width) / 2

            draw.text(
                (line_x, current_y - bbox[1]),
                line,
                font=current_font,
                fill=fill,
            )

            current_y += line_height + 3

    def _wrap_text_by_pixels(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        max_width: int,
        *,
        max_lines: int,
    ) -> list[str]:
        words = text.split()

        if not words:
            return [""]

        lines: list[str] = []
        current = ""

        for word in words:
            candidate = word if not current else f"{current} {word}"
            bbox = draw.textbbox((0, 0), candidate, font=font)
            candidate_width = bbox[2] - bbox[0]

            if candidate_width <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                    current = word
                else:
                    lines.append(word)
                    current = ""

            if len(lines) >= max_lines:
                break

        if current and len(lines) < max_lines:
            lines.append(current)

        original_joined = " ".join(words)
        visible_joined = " ".join(lines)

        if visible_joined != original_joined and lines:
            last = lines[-1]

            while last and draw.textbbox((0, 0), last + "…", font=font)[2] > max_width:
                last = last[:-1]

            lines[-1] = last + "…"

        return lines[:max_lines]

    def _line_height(
        self,
        draw: ImageDraw.ImageDraw,
        font: ImageFont.ImageFont,
    ) -> int:
        bbox = draw.textbbox((0, 0), "Ag", font=font)
        return bbox[3] - bbox[1]


# ============================================================
# PARTE 2 — DISCORD.PY: MODALS, SELECTS, VIEWS E SESSÃO
# ============================================================

class ConfigureTiersModal(discord.ui.Modal):
    def __init__(self, cog: TierListCog, owner_id: int) -> None:
        super().__init__(title="📝 Configurar Tiers")

        self.cog = cog
        self.owner_id = owner_id

        self.tiers_input = discord.ui.TextInput(
            label="Tiers separadas por vírgula",
            placeholder="Exemplo: S, A, B, C, D",
            default="S, A, B, C, D",
            min_length=1,
            max_length=250,
            required=True,
        )

        self.add_item(self.tiers_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        session = self.cog.sessions.get(self.owner_id)

        if session is None:
            await interaction.response.send_message(
                "❌ Essa sessão expirou ou foi cancelada.",
                ephemeral=True,
            )
            return

        parsed = self.cog.parse_tiers(str(self.tiers_input.value))

        if not parsed:
            await interaction.response.send_message(
                "⚠️ Você precisa informar pelo menos uma tier válida.",
                ephemeral=True,
            )
            return

        if len(parsed) > 25:
            await interaction.response.send_message(
                "⚠️ Use no máximo **25 tiers**, pois esse é o limite do Select Menu do Discord.",
                ephemeral=True,
            )
            return

        old_items = session.items
        new_items: OrderedDictType[str, list[TierItem]] = OrderedDict(
            (tier, []) for tier in parsed
        )

        # Preserva itens das tiers que ainda existem.
        for tier in parsed:
            if tier in old_items:
                new_items[tier].extend(old_items[tier])

        # Se uma tier antiga foi removida, os itens dela vão para a primeira tier nova.
        # Assim o usuário não perde dados silenciosamente.
        removed_items: list[TierItem] = []

        for old_tier, items in old_items.items():
            if old_tier not in new_items:
                removed_items.extend(items)

        if removed_items:
            new_items[parsed[0]].extend(removed_items)

        session.tiers = parsed
        session.items = new_items

        await self.cog.refresh_panel(session)

        await interaction.response.send_message(
            f"✅ Tiers configuradas: **{', '.join(parsed)}**",
            ephemeral=True,
        )


class AddItemModal(discord.ui.Modal):
    def __init__(self, cog: TierListCog, owner_id: int) -> None:
        super().__init__(title="➕ Adicionar Item")

        self.cog = cog
        self.owner_id = owner_id

        self.item_name = discord.ui.TextInput(
            label="Nome do item",
            placeholder="Exemplo: Pizza, Minecraft, Billie...",
            min_length=1,
            max_length=25,
            required=True,
        )

        self.image_url = discord.ui.TextInput(
            label="URL da imagem",
            placeholder="Opcional: https://exemplo.com/imagem.png",
            min_length=0,
            max_length=500,
            required=False,
        )

        self.add_item(self.item_name)
        self.add_item(self.image_url)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        session = self.cog.sessions.get(self.owner_id)

        if session is None:
            await interaction.response.send_message(
                "❌ Essa sessão expirou ou foi cancelada.",
                ephemeral=True,
            )
            return

        if not session.tiers:
            await interaction.response.send_message(
                "⚠️ Configure as tiers antes de adicionar itens.",
                ephemeral=True,
            )
            return

        total_items = sum(len(items) for items in session.items.values())

        if total_items >= self.cog.MAX_ITEMS_PER_SESSION:
            await interaction.response.send_message(
                f"⚠️ Limite de **{self.cog.MAX_ITEMS_PER_SESSION} itens** atingido nessa tier list.",
                ephemeral=True,
            )
            return

        clean_item = self.cog.clean_text(str(self.item_name.value), max_length=25)
        clean_url = str(self.image_url.value).strip() or None

        # Não tenta validar baixando aqui.
        # Só descarta URLs obviamente inválidas.
        # O download real acontece no botão Gerar Imagem.
        if clean_url and not self.cog.looks_like_url(clean_url):
            clean_url = None

        item = TierItem(
            name=clean_item,
            image_url=clean_url,
        )

        view = ItemTierSelectView(
            cog=self.cog,
            owner_id=self.owner_id,
            item=item,
            tiers=session.tiers,
        )

        extra = (
            "\n🖼️ Imagem detectada. Se o link falhar, o bot usa card de texto automaticamente."
            if clean_url
            else ""
        )

        await interaction.response.send_message(
            f"📌 Escolha em qual tier colocar **{discord.utils.escape_markdown(clean_item)}**:{extra}",
            view=view,
            ephemeral=True,
        )


class ItemTierSelect(discord.ui.Select):
    def __init__(
        self,
        cog: TierListCog,
        owner_id: int,
        item: TierItem,
        tiers: list[str],
    ) -> None:
        self.cog = cog
        self.owner_id = owner_id
        self.item = item

        options = [
            discord.SelectOption(
                label=tier[:100],
                value=tier,
                description=f"Colocar em {tier}"[:100],
                emoji="📌",
            )
            for tier in tiers[:25]
        ]

        super().__init__(
            placeholder="Selecione a tier do item",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        session = self.cog.sessions.get(self.owner_id)

        if session is None:
            await interaction.response.edit_message(
                content="❌ Essa sessão expirou ou foi cancelada.",
                view=None,
            )
            return

        selected_tier = self.values[0]

        # Blindagem:
        # O Select foi criado com as tiers da sessão,
        # mas ainda validamos novamente no callback.
        if selected_tier not in session.tiers or selected_tier not in session.items:
            await interaction.response.edit_message(
                content="❌ Essa tier não existe mais na sessão atual. Configure novamente.",
                view=None,
            )
            return

        session.items[selected_tier].append(self.item)

        await self.cog.refresh_panel(session)

        image_badge = " com imagem" if self.item.image_url else ""

        await interaction.response.edit_message(
            content=(
                f"✅ **{discord.utils.escape_markdown(self.item.name)}**{image_badge} "
                f"foi adicionado em **{selected_tier}**."
            ),
            view=None,
        )


class ItemTierSelectView(discord.ui.View):
    def __init__(
        self,
        cog: TierListCog,
        owner_id: int,
        item: TierItem,
        tiers: list[str],
    ) -> None:
        super().__init__(timeout=120)

        self.cog = cog
        self.owner_id = owner_id

        self.add_item(
            ItemTierSelect(
                cog=cog,
                owner_id=owner_id,
                item=item,
                tiers=tiers,
            )
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ Só quem criou a tier list pode usar esse menu.",
                ephemeral=True,
            )
            return False

        return True


class TierListControlView(discord.ui.View):
    def __init__(self, cog: TierListCog, owner_id: int) -> None:
        super().__init__(timeout=15 * 60)

        self.cog = cog
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ Esse painel não é seu, beldade. Crie o seu com `/tierlist criar`.",
                ephemeral=True,
            )
            return False

        if self.owner_id not in self.cog.sessions:
            await interaction.response.send_message(
                "❌ Essa sessão expirou ou foi cancelada.",
                ephemeral=True,
            )
            return False

        return True

    async def on_timeout(self) -> None:
        # Remove a sessão da RAM.
        # Isso evita memory leak caso a pessoa abandone o painel.
        session = self.cog.sessions.pop(self.owner_id, None)

        for child in self.children:
            child.disabled = True

        if session and session.panel_message:
            try:
                embed = discord.Embed(
                    title="⌛ Sessão Expirada",
                    description="A criação dessa tier list ficou inativa por muito tempo e foi encerrada.",
                    color=discord.Color.dark_gray(),
                )

                await session.panel_message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

        self.stop()

    @discord.ui.button(
        label="Configurar Tiers",
        emoji="📝",
        style=discord.ButtonStyle.primary,
    )
    async def configure_tiers(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(
            ConfigureTiersModal(
                cog=self.cog,
                owner_id=self.owner_id,
            )
        )

    @discord.ui.button(
        label="Adicionar Item",
        emoji="➕",
        style=discord.ButtonStyle.success,
    )
    async def add_item(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        session = self.cog.sessions.get(self.owner_id)

        if session is None:
            await interaction.response.send_message(
                "❌ Essa sessão expirou ou foi cancelada.",
                ephemeral=True,
            )
            return

        if not session.tiers:
            await interaction.response.send_message(
                "⚠️ Configure as tiers antes de adicionar itens.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            AddItemModal(
                cog=self.cog,
                owner_id=self.owner_id,
            )
        )

    @discord.ui.button(
        label="Gerar Imagem",
        emoji="🖼️",
        style=discord.ButtonStyle.secondary,
    )
    async def generate_image(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        session = self.cog.sessions.get(self.owner_id)

        if session is None:
            await interaction.response.send_message(
                "❌ Essa sessão expirou ou foi cancelada.",
                ephemeral=True,
            )
            return

        total_items = sum(len(items) for items in session.items.values())

        if total_items <= 0:
            await interaction.response.send_message(
                "⚠️ Adicione pelo menos **1 item** antes de gerar a imagem.",
                ephemeral=True,
            )
            return

        # Responde imediatamente ao Discord.
        # Isso evita timeout da interação enquanto baixamos imagens e renderizamos.
        await interaction.response.defer(thinking=True)

        title_snapshot = session.title

        # Copia a sessão para evitar alteração enquanto renderiza.
        tiers_snapshot: OrderedDictType[str, list[TierItem]] = OrderedDict(
            (
                tier,
                [
                    TierItem(
                        name=item.name,
                        image_url=item.image_url,
                        image_bytes=None,
                    )
                    for item in session.items.get(tier, [])
                ],
            )
            for tier in session.tiers
        )

        try:
            # Download assíncrono das URLs.
            hydrated_snapshot = await self.cog.hydrate_tier_images(tiers_snapshot)

            # Pillow fora do event loop.
            image_buffer = await asyncio.to_thread(
                self.cog.renderer.generate_tierlist_image,
                title_snapshot,
                hydrated_snapshot,
            )

        except Exception:
            await interaction.followup.send(
                "❌ Não consegui gerar a imagem final dessa vez.",
                ephemeral=True,
            )
            return

        file = discord.File(
            image_buffer,
            filename="tierlist.png",
        )

        # Limpa a sessão da memória.
        self.cog.sessions.pop(self.owner_id, None)

        for child in self.children:
            child.disabled = True

        try:
            if session.panel_message:
                done_embed = discord.Embed(
                    title="✅ Tier List Gerada",
                    description="A imagem final foi criada e a sessão foi encerrada.",
                    color=discord.Color.green(),
                )

                await session.panel_message.edit(embed=done_embed, view=self)
        except discord.HTTPException:
            pass

        self.stop()

        await interaction.followup.send(
            content=f"🖼️ **{discord.utils.escape_markdown(title_snapshot)}**",
            file=file,
        )

    @discord.ui.button(
        label="Cancelar",
        emoji="❌",
        style=discord.ButtonStyle.danger,
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.cog.sessions.pop(self.owner_id, None)

        for child in self.children:
            child.disabled = True

        self.stop()

        await interaction.response.defer(ephemeral=True)

        try:
            await interaction.message.delete()

            await interaction.followup.send(
                "❌ Sessão cancelada e painel removido.",
                ephemeral=True,
            )

        except discord.HTTPException:
            try:
                embed = discord.Embed(
                    title="❌ Sessão Cancelada",
                    description="Os dados temporários foram apagados.",
                    color=discord.Color.red(),
                )

                await interaction.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

            await interaction.followup.send(
                "❌ Sessão cancelada.",
                ephemeral=True,
            )


# ============================================================
# COG PRINCIPAL
# ============================================================

class TierListCog(
    commands.GroupCog,
    group_name="tierlist",
    group_description="Crie Tier Lists Interativas",
):
    MAX_TIERS = 25
    MAX_ITEMS_PER_SESSION = 150

    MAX_IMAGE_BYTES = 5 * 1024 * 1024
    IMAGE_DOWNLOAD_TIMEOUT = 5
    IMAGE_DOWNLOAD_CONCURRENCY = 8

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        # Estado temporário:
        # user_id -> TierListSession
        self.sessions: dict[int, TierListSession] = {}

        self.renderer = TierListRenderer()

    @app_commands.command(
        name="criar",
        description="Cria uma tier list interativa com texto e imagens por URL.",
    )
    @app_commands.guild_only()
    @app_commands.describe(titulo="Título da tier list")
    async def criar(
        self,
        interaction: discord.Interaction,
        titulo: app_commands.Range[str, 1, 80],
    ) -> None:
        owner_id = interaction.user.id
        clean_title = self.clean_text(str(titulo), max_length=80)

        # Se o usuário já tinha sessão, substitui com segurança.
        old_session = self.sessions.pop(owner_id, None)

        if old_session and old_session.panel_message:
            try:
                old_embed = discord.Embed(
                    title="♻️ Sessão Substituída",
                    description="Você iniciou uma nova tier list, então essa sessão antiga foi encerrada.",
                    color=discord.Color.dark_gray(),
                )

                await old_session.panel_message.edit(embed=old_embed, view=None)
            except discord.HTTPException:
                pass

        session = TierListSession(
            owner_id=owner_id,
            title=clean_title,
        )

        self.sessions[owner_id] = session

        view = TierListControlView(
            cog=self,
            owner_id=owner_id,
        )

        embed = self.build_panel_embed(session)

        await interaction.response.send_message(
            embed=embed,
            view=view,
        )

        session.panel_message = await interaction.original_response()

    async def fetch_image_safely(
        self,
        http: aiohttp.ClientSession,
        url: str,
    ) -> bytes | None:
        """
        Baixa uma imagem sem derrubar o bot.

        Regras:
        - aceita somente http/https;
        - timeout vem do ClientSession;
        - status precisa ser 200;
        - Content-Type precisa começar com image/;
        - limite de 5 MB;
        - qualquer erro retorna None;
        - None vira fallback silencioso para texto.
        """

        if not self.looks_like_url(url):
            return None

        try:
            async with http.get(url, allow_redirects=True) as response:
                if response.status != 200:
                    return None

                content_type = response.headers.get("Content-Type", "").lower()

                if not content_type.startswith("image/"):
                    return None

                data = bytearray()

                async for chunk in response.content.iter_chunked(64 * 1024):
                    data.extend(chunk)

                    if len(data) > self.MAX_IMAGE_BYTES:
                        return None

                if not data:
                    return None

                return bytes(data)

        except Exception:
            return None

    async def hydrate_tier_images(
        self,
        tiers_snapshot: OrderedDictType[str, list[TierItem]],
    ) -> OrderedDictType[str, list[TierItem]]:
        """
        Baixa todas as imagens antes do Pillow.

        Importante:
        - aiohttp roda de forma assíncrona;
        - renderização Pillow roda depois em thread separada;
        - imagem inválida vira None;
        - None é fallback para card de texto.
        """

        timeout = aiohttp.ClientTimeout(total=self.IMAGE_DOWNLOAD_TIMEOUT)
        semaphore = asyncio.Semaphore(self.IMAGE_DOWNLOAD_CONCURRENCY)

        async with aiohttp.ClientSession(timeout=timeout) as http:

            async def hydrate_one(item: TierItem) -> None:
                if not item.image_url:
                    return

                async with semaphore:
                    item.image_bytes = await self.fetch_image_safely(http, item.image_url)

            tasks: list[asyncio.Task[None]] = []

            for items in tiers_snapshot.values():
                for item in items:
                    if item.image_url:
                        tasks.append(asyncio.create_task(hydrate_one(item)))

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        return tiers_snapshot

    def parse_tiers(self, raw: str) -> list[str]:
        """
        Entrada:
            S, A, B, C, D

        Saída:
            ["S", "A", "B", "C", "D"]

        Remove vazios, normaliza espaços e evita duplicatas.
        """

        parts = raw.split(",")
        tiers: list[str] = []
        seen: set[str] = set()

        for part in parts:
            clean = self.clean_text(part, max_length=20)

            if not clean:
                continue

            key = clean.casefold()

            if key in seen:
                continue

            seen.add(key)
            tiers.append(clean)

            if len(tiers) >= self.MAX_TIERS:
                break

        return tiers

    def clean_text(
        self,
        text: str,
        *,
        max_length: int,
    ) -> str:
        """
        Limpa texto do usuário:
        - remove quebras de linha;
        - comprime espaços repetidos;
        - corta no limite seguro.
        """

        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_length].strip()

    def looks_like_url(self, url: str) -> bool:
        parsed = urlparse(url)

        return (
            parsed.scheme in {"http", "https"}
            and bool(parsed.netloc)
        )

    def build_panel_embed(self, session: TierListSession) -> discord.Embed:
        total_items = sum(len(items) for items in session.items.values())
        image_items = sum(
            1
            for items in session.items.values()
            for item in items
            if item.image_url
        )

        embed = discord.Embed(
            title="🧩 Painel De Criação De Tier List",
            description=(
                f"**Título:** {discord.utils.escape_markdown(session.title)}\n"
                f"**Tiers:** {len(session.tiers)}\n"
                f"**Itens:** {total_items}/{self.MAX_ITEMS_PER_SESSION}\n"
                f"**Itens Com URL:** {image_items}\n\n"
                "Use os botões abaixo para configurar, adicionar itens e gerar a imagem final."
            ),
            color=discord.Color.from_rgb(155, 93, 229),
        )

        for tier in session.tiers:
            items = session.items.get(tier, [])

            preview_parts: list[str] = []

            for item in items[:8]:
                icon = "🖼️" if item.image_url else "📝"
                preview_parts.append(f"{icon} {item.name}")

            preview = ", ".join(preview_parts)

            if len(items) > 8:
                preview += f" +{len(items) - 8}"

            embed.add_field(
                name=f"📌 {tier}",
                value=preview or "Sem itens ainda",
                inline=False,
            )

        embed.set_footer(
            text="A sessão expira após 15 minutos de inatividade. URLs ruins viram texto automaticamente."
        )

        return embed

    async def refresh_panel(self, session: TierListSession) -> None:
        if not session.panel_message:
            return

        try:
            await session.panel_message.edit(embed=self.build_panel_embed(session))
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TierListCog(bot))