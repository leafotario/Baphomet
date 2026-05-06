from __future__ import annotations

from collections.abc import Iterable

from .models import FieldDefinition, ProfileFieldType


class UnknownProfileFieldError(ValueError):
    pass


class FieldRegistry:
    """Catalogo declarativo dos campos persistidos da ficha.

    O registry e a fonte unica para validacao, UX, moderacao e renderizacao.
    Campos vivos como nome, avatar, XP, level e cargos nao entram aqui.
    """

    def __init__(self, definitions: Iterable[FieldDefinition]) -> None:
        self._definitions: dict[str, FieldDefinition] = {}
        for definition in definitions:
            normalized_key = definition.key.strip().casefold()
            if not normalized_key:
                raise ValueError("profile field key nao pode ser vazio")
            if normalized_key in self._definitions:
                raise ValueError(f"profile field duplicado: {normalized_key}")
            self._definitions[normalized_key] = definition

    def get(self, key: str) -> FieldDefinition:
        normalized_key = key.strip().casefold()
        definition = self._definitions.get(normalized_key)
        if definition is None:
            raise UnknownProfileFieldError(f"campo de ficha desconhecido: {key}")
        return definition

    def all(self) -> tuple[FieldDefinition, ...]:
        return tuple(self._definitions.values())

    def keys(self) -> tuple[str, ...]:
        return tuple(self._definitions)

    def choices_for_autocomplete(self, query: str = "") -> list[tuple[str, str]]:
        normalized_query = query.strip().casefold()
        choices: list[tuple[str, str]] = []
        for definition in self._definitions.values():
            if normalized_query and normalized_query not in definition.key and normalized_query not in definition.label.casefold():
                continue
            choices.append((definition.label, definition.key))
        return choices[:25]


PROFILE_FIELD_REGISTRY = FieldRegistry(
    (
        FieldDefinition(
            key="pronouns",
            label="Pronomes",
            field_type=ProfileFieldType.TEXT_SHORT,
            max_length=64,
            accepts_auto_sync=True,
            moderation_fallback="",
            rendered=True,
            user_editable=True,
            moderation_admin=True,
        ),
        FieldDefinition(
            key="headline",
            label="Headline",
            field_type=ProfileFieldType.TEXT_SHORT,
            max_length=120,
            accepts_auto_sync=True,
            moderation_fallback="",
            rendered=True,
            user_editable=True,
            moderation_admin=True,
        ),
        FieldDefinition(
            key="basic_info",
            label="Info basica",
            field_type=ProfileFieldType.TEXT_LONG,
            max_length=600,
            accepts_auto_sync=True,
            moderation_fallback="",
            rendered=True,
            user_editable=True,
            moderation_admin=True,
        ),
        FieldDefinition(
            key="bio",
            label="Bio",
            field_type=ProfileFieldType.TEXT_LONG,
            max_length=1000,
            accepts_auto_sync=True,
            moderation_fallback="",
            rendered=True,
            user_editable=True,
            moderation_admin=True,
        ),
        FieldDefinition(
            key="ask_me_about",
            label="Me pergunte sobre",
            field_type=ProfileFieldType.TEXT_LONG,
            max_length=400,
            accepts_auto_sync=True,
            moderation_fallback="",
            rendered=True,
            user_editable=True,
            moderation_admin=True,
        ),
        FieldDefinition(
            key="mood",
            label="Mood",
            field_type=ProfileFieldType.TEXT_SHORT,
            max_length=80,
            accepts_auto_sync=True,
            moderation_fallback="",
            rendered=True,
            user_editable=True,
            moderation_admin=True,
        ),
        FieldDefinition(
            key="interests",
            label="Interesses",
            field_type=ProfileFieldType.TAG_LIST,
            max_length=32,
            accepts_auto_sync=True,
            moderation_fallback=(),
            rendered=True,
            user_editable=True,
            moderation_admin=True,
            max_items=12,
        ),
        FieldDefinition(
            key="theme_preset",
            label="Tema",
            field_type=ProfileFieldType.ENUM,
            max_length=32,
            accepts_auto_sync=False,
            moderation_fallback="classic",
            rendered=True,
            user_editable=True,
            moderation_admin=True,
            choices=("classic", "minimal", "neon", "celestial", "monochrome"),
        ),
        FieldDefinition(
            key="accent_palette",
            label="Paleta de destaque",
            field_type=ProfileFieldType.TAG_LIST,
            max_length=9,
            accepts_auto_sync=False,
            moderation_fallback=(),
            rendered=True,
            user_editable=True,
            moderation_admin=True,
            max_items=5,
        ),
        FieldDefinition(
            key="charm_preset",
            label="Charm",
            field_type=ProfileFieldType.ENUM,
            max_length=32,
            accepts_auto_sync=False,
            moderation_fallback="none",
            rendered=True,
            user_editable=True,
            moderation_admin=True,
            choices=("none", "stars", "vinyl", "laurels", "runes", "glitch"),
        ),
    )
)
