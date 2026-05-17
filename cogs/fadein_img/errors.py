from __future__ import annotations


class FadeInImageError(Exception):
    """Base error with a safe message for Discord users."""

    def __init__(
        self,
        user_message: str,
        *,
        code: str = "fadein_img_error",
        detail: str | None = None,
    ) -> None:
        super().__init__(detail or user_message)
        self.user_message = user_message
        self.code = code
        self.detail = detail or user_message


class UnsupportedMediaError(FadeInImageError):
    """The attachment is not a supported static image format."""


class InvalidImageError(FadeInImageError):
    """The image payload is empty, corrupt, or not a real image."""


class AnimatedImageNotSupportedError(FadeInImageError):
    """The payload is animated, but this command only supports static images."""


class ImageTooLargeError(FadeInImageError):
    """The input image exceeds configured safety limits."""


class OutputTooLargeError(FadeInImageError):
    """The generated GIF exceeds configured output limits."""


class ImageProcessingTimeoutError(FadeInImageError):
    """The image processing task exceeded the configured timeout."""
