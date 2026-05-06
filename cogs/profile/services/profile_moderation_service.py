from __future__ import annotations

from ..field_registry import FieldRegistry
from ..models import FieldDefinition, ProfileFieldStatus, ProfileFieldValue


class ProfileModerationService:
    REMOVED_CONTENT_PLACEHOLDER = "[Conteúdo removido]"

    def __init__(self, field_registry: FieldRegistry) -> None:
        self.field_registry = field_registry

    def fallback_for(self, definition: FieldDefinition) -> object:
        return definition.moderation_fallback

    def should_render_original(self, field: ProfileFieldValue | None) -> bool:
        return field is not None and field.status is ProfileFieldStatus.ACTIVE

    def render_value_for(self, field: ProfileFieldValue | None, raw_value: object, fallback: object) -> object:
        if field is None:
            return fallback
        if field.status is ProfileFieldStatus.REMOVED_BY_MOD:
            return self.REMOVED_CONTENT_PLACEHOLDER
        if field.status is ProfileFieldStatus.ACTIVE:
            return raw_value
        return fallback

    def assert_can_moderate(self, field_key: str) -> FieldDefinition:
        definition = self.field_registry.get(field_key)
        if not definition.moderation_admin:
            raise PermissionError(f"campo nao administravel pela moderacao: {field_key}")
        return definition
