from __future__ import annotations


class TemplateItemResolveError(Exception):
    def __init__(self, user_message: str, *, detail: str | None = None, code: str = "template_item_resolve_error") -> None:
        super().__init__(detail or user_message)
        self.user_message = user_message
        self.detail = detail or user_message
        self.code = code


class AssetDownloadError(TemplateItemResolveError):
    pass


class AssetValidationError(TemplateItemResolveError):
    pass


class ConflictingImageSourcesError(TemplateItemResolveError):
    pass


class EmptyTemplateItemError(TemplateItemResolveError):
    pass


class UnsupportedImageTypeError(AssetValidationError):
    pass


class UnsafeWikipediaImageError(TemplateItemResolveError):
    pass
