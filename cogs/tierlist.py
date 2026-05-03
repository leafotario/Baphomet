from __future__ import annotations

import asyncio
import io
import math
import pathlib
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import OrderedDict as OrderedDictType

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont


# ============================================================
# PARTE 1 — RENDERER PILLOW
# Matemática da imagem dinâmica com quebra de linha por Tier.
# ============================================================

class TierListRenderer:
    """
    Classe responsável APENAS por gerar a imagem final da tier list.

    Ela não sabe nada sobre Discord, Interactions, Views ou Modals.
    Isso deixa o código modular e fácil de testar separadamente.
    """

    WIDTH = 1200

    # Layout geral
    OUTER_PADDING = 28
    TITLE_HEIGHT = 110
    TIER_LABEL_WIDTH = 150
    TIER_GAP = 8

    # Área interna de cada fileira
    ROW_PADDING_X = 18
    ROW_PADDING_Y = 16

    # Cards de texto
    ITEM_WIDTH = 170
    ITEM_HEIGHT = 62
    ITEM_GAP_X = 12
    ITEM_GAP_Y = 12

    # Altura mínima da fileira mesmo se ela tiver poucos itens
    MIN_ROW_HEIGHT = 96

    # Cores base
    BACKGROUND = "#111116"
    ROW_BACKGROUND = "#202026"
    CARD_BACKGROUND = "#303039"
    CARD_OUTLINE = "#454552"
    TEXT = "#FFFFFF"
    MUTED_TEXT = "#A7A7B3"

    # Cores vibrantes das tiers.
    # Se tiver mais tiers que cores, o código recicla a lista usando módulo.
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
        tiers_dict: OrderedDictType[str, list[str]],
    ) -> io.BytesIO:
        """
        Gera a imagem final da Tier List.

        Parâmetros:
        - title: título da tier list.
        - tiers_dict: OrderedDict no formato:
            {
                "S": ["Item 1", "Item 2"],
                "A": ["Item 3"],
                ...
            }

        Retorna:
        - BytesIO pronto para ser enviado como discord.File.
        """

        # Imagem temporária só para termos um ImageDraw disponível.
        # Precisamos dele ANTES de criar a imagem final porque a altura final
        # depende de cálculos de texto/layout.
        measuring_img = Image.new("RGB", (self.WIDTH, 10), self.BACKGROUND)
        draw = ImageDraw.Draw(measuring_img)

        title_font = self._load_font(44, bold=True)
        tier_font = self._load_font(42, bold=True)
        item_font = self._load_font(24, bold=True)
        empty_font = self._load_font(22, bold=False)

        # ------------------------------------------------------------
        # CÁLCULO DA LARGURA ÚTIL DOS ITENS
        # ------------------------------------------------------------
        #
        # A largura total da imagem é 1200px.
        #
        # Dentro dela temos:
        # - OUTER_PADDING dos dois lados.
        # - Uma coluna fixa para o nome da tier.
        # - Padding interno da área onde os cards ficam.
        #
        # Tudo que sobra é a "items_area_width", ou seja,
        # a largura máxima onde os cards podem ser desenhados.
        #
        # Essa variável é o coração da quebra de linha.
        # Nenhum card pode ultrapassar essa largura.
        # ------------------------------------------------------------

        items_area_width = (
            self.WIDTH
            - self.OUTER_PADDING * 2
            - self.TIER_LABEL_WIDTH
            - self.ROW_PADDING_X * 2
        )

        # ------------------------------------------------------------
        # CÁLCULO DE QUANTOS CARDS CABEM POR LINHA
        # ------------------------------------------------------------
        #
        # Cada item ocupa:
        # - ITEM_WIDTH
        # - ITEM_GAP_X, exceto depois do último item da linha.
        #
        # A fórmula:
        #
        #   floor((largura_disponivel + gap) / (largura_card + gap))
        #
        # Por que somar +gap no numerador?
        # Porque numa linha com N cards existem apenas N-1 gaps.
        # Essa fórmula é uma forma clássica de compensar esse último gap
        # que não existe visualmente.
        #
        # Exemplo:
        # - área = 960
        # - card = 170
        # - gap = 12
        #
        # floor((960 + 12) / (170 + 12)) = floor(972 / 182) = 5
        #
        # Então cabem 5 cards:
        # 5*170 + 4*12 = 850 + 48 = 898px
        #
        # Se tentasse 6:
        # 6*170 + 5*12 = 1020 + 60 = 1080px
        # Estouraria a imagem.
        # ------------------------------------------------------------

        items_per_line = max(
            1,
            math.floor((items_area_width + self.ITEM_GAP_X) / (self.ITEM_WIDTH + self.ITEM_GAP_X)),
        )

        # ------------------------------------------------------------
        # PRÉ-CÁLCULO DE ALTURA DE CADA TIER
        # ------------------------------------------------------------
        #
        # Cada tier pode ter uma quantidade diferente de itens.
        # Logo, cada fileira pode ter uma altura diferente.
        #
        # Para cada tier:
        #
        #   line_count = ceil(total_itens / items_per_line)
        #
        # Se a Tier A tem 15 itens e cabem 5 por linha:
        #
        #   ceil(15 / 5) = 3 linhas
        #
        # A altura de conteúdo vira:
        #
        #   linhas * altura_card
        #   + gaps verticais entre linhas
        #   + padding superior/inferior da fileira
        #
        # Essa é a parte que impede os itens de vazarem para fora
        # da imagem ou por cima da próxima tier.
        # ------------------------------------------------------------

        row_layouts: list[dict] = []

        for index, (tier_name, items) in enumerate(tiers_dict.items()):
            item_count = len(items)

            # Mesmo uma tier vazia precisa de 1 linha visual,
            # para mostrar "Sem itens ainda" e manter a estética.
            line_count = max(1, math.ceil(item_count / items_per_line))

            cards_height = line_count * self.ITEM_HEIGHT
            gaps_height = max(0, line_count - 1) * self.ITEM_GAP_Y
            content_height = cards_height + gaps_height + self.ROW_PADDING_Y * 2

            # A altura real da tier é a maior entre:
            # - altura mínima estética
            # - altura necessária para todas as linhas de cards
            row_height = max(self.MIN_ROW_HEIGHT, content_height)

            row_layouts.append(
                {
                    "tier": tier_name,
                    "items": items,
                    "color": self.TIER_COLORS[index % len(self.TIER_COLORS)],
                    "line_count": line_count,
                    "row_height": row_height,
                    "items_per_line": items_per_line,
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

        # Título
        self._draw_centered_text(
            draw=draw,
            box=(self.OUTER_PADDING, 0, self.WIDTH - self.OUTER_PADDING, self.TITLE_HEIGHT),
            text=title,
            font=title_font,
            fill=self.TEXT,
            max_width=self.WIDTH - self.OUTER_PADDING * 2,
        )

        y = self.TITLE_HEIGHT + self.OUTER_PADDING

        for row in row_layouts:
            tier_name = row["tier"]
            items = row["items"]
            color = row["color"]
            row_height = row["row_height"]
            items_per_line = row["items_per_line"]

            row_x1 = self.OUTER_PADDING
            row_y1 = y
            row_x2 = self.WIDTH - self.OUTER_PADDING
            row_y2 = y + row_height

            # Fundo da fileira
            draw.rounded_rectangle(
                (row_x1, row_y1, row_x2, row_y2),
                radius=18,
                fill=self.ROW_BACKGROUND,
            )

            # Bloco colorido da tier
            label_x1 = row_x1
            label_y1 = row_y1
            label_x2 = row_x1 + self.TIER_LABEL_WIDTH
            label_y2 = row_y2

            draw.rounded_rectangle(
                (label_x1, label_y1, label_x2, label_y2),
                radius=18,
                fill=color,
            )

            # Retângulo de correção para a borda direita do label não ficar arredondada.
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
                )
            else:
                for item_index, item_text in enumerate(items):
                    # ------------------------------------------------------------
                    # POSICIONAMENTO MATEMÁTICO DE CADA CARD
                    # ------------------------------------------------------------
                    #
                    # A posição do item é calculada por índice.
                    #
                    # col = índice % cards_por_linha
                    # row = índice // cards_por_linha
                    #
                    # Exemplo com 5 cards por linha:
                    #
                    # índice 0 → row 0, col 0
                    # índice 4 → row 0, col 4
                    # índice 5 → row 1, col 0
                    #
                    # Assim, quando passa do limite horizontal,
                    # o item automaticamente cai para a próxima linha.
                    #
                    # O y da nova linha considera:
                    # - altura do card
                    # - gap vertical
                    #
                    # Isso garante que a Tier expanda para baixo
                    # sem atropelar a próxima fileira.
                    # ------------------------------------------------------------

                    item_row = item_index // items_per_line
                    item_col = item_index % items_per_line

                    item_x1 = items_start_x + item_col * (self.ITEM_WIDTH + self.ITEM_GAP_X)
                    item_y1 = items_start_y + item_row * (self.ITEM_HEIGHT + self.ITEM_GAP_Y)
                    item_x2 = item_x1 + self.ITEM_WIDTH
                    item_y2 = item_y1 + self.ITEM_HEIGHT

                    draw.rounded_rectangle(
                        (item_x1, item_y1, item_x2, item_y2),
                        radius=14,
                        fill=self.CARD_BACKGROUND,
                        outline=self.CARD_OUTLINE,
                        width=2,
                    )

                    self._draw_centered_wrapped_text(
                        draw=draw,
                        box=(item_x1 + 10, item_y1 + 6, item_x2 - 10, item_y2 - 6),
                        text=item_text,
                        font=item_font,
                        fill=self.TEXT,
                    )

            y += row_height + self.TIER_GAP

        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        buffer.seek(0)
        return buffer

    def _load_font(self, size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """
        Tenta carregar uma fonte TTF.
        Se falhar, usa a fonte padrão do Pillow.

        Você pode passar font_path no construtor se quiser usar uma fonte específica:
            TierListRenderer(font_path="assets/fonts/MinhaFonte.ttf")
        """

        candidates: list[str] = []

        if self.font_path:
            candidates.append(self.font_path)

        # Tenta pegar alguma fonte dentro de assets/fonts, já que teu repo tem essa pasta.
        assets_fonts = pathlib.Path("assets/fonts")
        if assets_fonts.exists():
            candidates.extend(str(path) for path in assets_fonts.glob("*.ttf"))

        # Fallbacks comuns em Linux/Windows.
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
    ) -> None:
        """
        Desenha texto centralizado usando textbbox.
        Se o texto for largo demais, diminui a fonte progressivamente.
        """

        x1, y1, x2, y2 = box
        current_font = font

        # Tenta reduzir fonte quando for FreeTypeFont.
        if isinstance(font, ImageFont.FreeTypeFont):
            size = font.size
            while size > 12:
                bbox = draw.textbbox((0, 0), text, font=current_font)
                text_width = bbox[2] - bbox[0]
                if text_width <= max_width:
                    break

                size -= 2
                current_font = self._load_font(size, bold=True)

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
        """
        Centraliza texto dentro de um card.
        Como o Modal limita o item a 25 caracteres, normalmente 1 ou 2 linhas bastam.
        """

        x1, y1, x2, y2 = box
        max_width = x2 - x1
        max_height = y2 - y1

        current_font = font

        if isinstance(font, ImageFont.FreeTypeFont):
            size = font.size

            while size >= 12:
                current_font = self._load_font(size, bold=True)
                lines = self._wrap_text_by_pixels(draw, text, current_font, max_width, max_lines=2)
                line_height = self._line_height(draw, current_font)
                total_height = len(lines) * line_height + max(0, len(lines) - 1) * 3

                widest_line = max(
                    (draw.textbbox((0, 0), line, font=current_font)[2] for line in lines),
                    default=0,
                )

                if widest_line <= max_width and total_height <= max_height:
                    break

                size -= 2
        else:
            lines = [text]

        line_height = self._line_height(draw, current_font)
        total_text_height = len(lines) * line_height + max(0, len(lines) - 1) * 3

        current_y = y1 + (max_height - total_text_height) / 2

        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=current_font)
            line_width = bbox[2] - bbox[0]

            line_x = x1 + (max_width - line_width) / 2
            draw.text((line_x, current_y - bbox[1]), line, font=current_font, fill=fill)

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
        """
        Quebra texto por largura real em pixels, não por número fixo de caracteres.
        Isso evita erro com letras largas como W/M e letras finas como i/l.
        """

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
                    # Palavra única enorme.
                    # Como o Modal já limita a 25 chars, isso quase nunca acontece,
                    # mas o fallback existe porque usuário sempre acha um jeito, né.
                    lines.append(word)
                    current = ""

            if len(lines) >= max_lines:
                break

        if current and len(lines) < max_lines:
            lines.append(current)

        # Se ainda sobrou texto, adiciona reticências na última linha.
        original_joined = " ".join(words)
        visible_joined = " ".join(lines)

        if visible_joined != original_joined and lines:
            last = lines[-1]
            while last and draw.textbbox((0, 0), last + "…", font=font)[2] > max_width:
                last = last[:-1]
            lines[-1] = last + "…"

        return lines[:max_lines]

    def _line_height(self, draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont) -> int:
        bbox = draw.textbbox((0, 0), "Ag", font=font)
        return bbox[3] - bbox[1]


# ============================================================
# PARTE 2 — DISCORD.PY
# Sessão, Modals, Views, Select Menu e comando /tierlist criar.
# ============================================================

@dataclass
class TierListSession:
    owner_id: int
    title: str
    tiers: list[str] = field(default_factory=lambda: ["S", "A", "B", "C", "D"])
    items: OrderedDictType[str, list[str]] = field(
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

        raw = str(self.tiers_input.value)
        parsed = self.cog.parse_tiers(raw)

        if not parsed:
            await interaction.response.send_message(
                "⚠️ Você precisa informar pelo menos uma tier válida.",
                ephemeral=True,
            )
            return

        if len(parsed) > 25:
            await interaction.response.send_message(
                "⚠️ O Discord só permite até **25 opções** em um Select Menu. Use no máximo 25 tiers.",
                ephemeral=True,
            )
            return

        old_items = session.items
        new_items: OrderedDictType[str, list[str]] = OrderedDict((tier, []) for tier in parsed)

        # Preserva itens de tiers que continuam existindo.
        for tier in parsed:
            if tier in old_items:
                new_items[tier].extend(old_items[tier])

        # Se o usuário removeu alguma tier antiga, não jogamos os itens fora.
        # Eles são movidos para a primeira tier nova, evitando perda silenciosa de dados.
        removed_items: list[str] = []
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
            placeholder="Exemplo: Pizza",
            min_length=1,
            max_length=25,
            required=True,
        )

        self.add_item(self.item_name)

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

        if not clean_item:
            await interaction.response.send_message(
                "⚠️ O item precisa ter algum texto válido.",
                ephemeral=True,
            )
            return

        view = ItemTierSelectView(
            cog=self.cog,
            owner_id=self.owner_id,
            item_name=clean_item,
            tiers=session.tiers,
        )

        await interaction.response.send_message(
            f"📌 Escolha em qual tier colocar **{discord.utils.escape_markdown(clean_item)}**:",
            view=view,
            ephemeral=True,
        )


class ItemTierSelect(discord.ui.Select):
    def __init__(
        self,
        cog: TierListCog,
        owner_id: int,
        item_name: str,
        tiers: list[str],
    ) -> None:
        self.cog = cog
        self.owner_id = owner_id
        self.item_name = item_name

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

        # Segurança importante:
        # o usuário só consegue selecionar tiers vindas da sessão atual.
        # Mesmo assim, validamos novamente aqui, porque nunca se confia 100% no client.
        if selected_tier not in session.tiers or selected_tier not in session.items:
            await interaction.response.edit_message(
                content="❌ Essa tier não existe mais na sessão atual. Configure novamente.",
                view=None,
            )
            return

        session.items[selected_tier].append(self.item_name)

        await self.cog.refresh_panel(session)

        await interaction.response.edit_message(
            content=f"✅ **{discord.utils.escape_markdown(self.item_name)}** foi adicionado em **{selected_tier}**.",
            view=None,
        )


class ItemTierSelectView(discord.ui.View):
    def __init__(
        self,
        cog: TierListCog,
        owner_id: int,
        item_name: str,
        tiers: list[str],
    ) -> None:
        super().__init__(timeout=120)

        self.cog = cog
        self.owner_id = owner_id

        self.add_item(
            ItemTierSelect(
                cog=cog,
                owner_id=owner_id,
                item_name=item_name,
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

    @discord.ui.button(label="Configurar Tiers", emoji="📝", style=discord.ButtonStyle.primary)
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

    @discord.ui.button(label="Adicionar Item", emoji="➕", style=discord.ButtonStyle.success)
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

    @discord.ui.button(label="Gerar Imagem", emoji="🖼️", style=discord.ButtonStyle.secondary)
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

        await interaction.response.defer(thinking=True)

        # Copia os dados antes de renderizar em thread separada.
        # Isso impede que uma interação simultânea altere a lista enquanto o Pillow renderiza.
        title_snapshot = session.title
        tiers_snapshot: OrderedDictType[str, list[str]] = OrderedDict(
            (tier, list(session.items.get(tier, [])))
            for tier in session.tiers
        )

        try:
            image_buffer = await asyncio.to_thread(
                self.cog.renderer.generate_tierlist_image,
                title_snapshot,
                tiers_snapshot,
            )
        except Exception:
            await interaction.followup.send(
                "❌ O Pillow tropeçou na passarela e não conseguiu gerar a imagem.",
                ephemeral=True,
            )
            return

        file = discord.File(
            image_buffer,
            filename="tierlist.png",
        )

        # Limpa memória depois de gerar.
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

    @discord.ui.button(label="Cancelar", emoji="❌", style=discord.ButtonStyle.danger)
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

        # Tenta excluir o painel como o usuário pediu.
        # Se falhar, apenas edita para cancelado.
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


class TierListCog(commands.GroupCog, group_name="tierlist", group_description="Crie Tier Lists Interativas"):
    MAX_TIERS = 25
    MAX_ITEMS_PER_SESSION = 150

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        # Estado temporário em RAM.
        # user_id -> TierListSession
        self.sessions: dict[int, TierListSession] = {}

        self.renderer = TierListRenderer()

    @app_commands.command(
        name="criar",
        description="Cria uma tier list interativa com painel, botões e imagem final.",
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

        # Se a pessoa já tinha uma sessão aberta, substitui com segurança.
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

    def parse_tiers(self, raw: str) -> list[str]:
        """
        Recebe texto tipo:
            S, A, B, C, D

        Retorna:
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

    def clean_text(self, text: str, *, max_length: int) -> str:
        """
        Limpa texto de usuário:
        - remove quebras de linha;
        - comprime espaços repetidos;
        - corta no limite seguro.
        """

        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_length].strip()

    def build_panel_embed(self, session: TierListSession) -> discord.Embed:
        total_items = sum(len(items) for items in session.items.values())

        embed = discord.Embed(
            title="🧩 Painel De Criação De Tier List",
            description=(
                f"**Título:** {discord.utils.escape_markdown(session.title)}\n"
                f"**Tiers:** {len(session.tiers)}\n"
                f"**Itens:** {total_items}/{self.MAX_ITEMS_PER_SESSION}\n\n"
                "Use os botões abaixo para configurar, adicionar itens e gerar a imagem final."
            ),
            color=discord.Color.from_rgb(155, 93, 229),
        )

        for tier in session.tiers:
            items = session.items.get(tier, [])
            preview = ", ".join(items[:8])

            if len(items) > 8:
                preview += f" +{len(items) - 8}"

            embed.add_field(
                name=f"📌 {tier}",
                value=preview or "Sem itens ainda",
                inline=False,
            )

        embed.set_footer(text="A sessão expira após 15 minutos de inatividade.")

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