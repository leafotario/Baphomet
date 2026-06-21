from __future__ import annotations


class WhiteTextError(Exception):
    """Base error with a message that can be shown safely to Discord users."""

    def __init__(
        self,
        user_message: str,
        *,
        code: str = "whitetext_error",
        detail: str | None = None,
    ) -> None:
        super().__init__(detail or user_message)
        self.user_message = user_message
        self.code = code
        self.detail = detail or user_message


class FontNotFoundError(WhiteTextError):
    """The required FuturaCEB.otf font could not be found or loaded."""


class TextTooLongError(WhiteTextError):
    """The caption exceeds the configured character limit."""


class InvalidTextError(WhiteTextError):
    """The caption text is missing or invalid after normalization."""


class LayoutComputationError(WhiteTextError):
    """The caption layout cannot be computed safely for the given width."""


class UnsupportedMediaError(WhiteTextError):
    """The media format is not supported by the requested processing path."""


class InvalidImageError(WhiteTextError):
    """The image payload is missing, corrupt, or not a real image."""


class ImageTooLargeError(WhiteTextError):
    """The input image exceeds configured safety limits."""


class OutputTooLargeError(WhiteTextError):
    """The rendered output exceeds configured upload/safety limits."""


class FFmpegNotFoundError(WhiteTextError):
    """FFmpeg or ffprobe is not installed or not available in PATH."""


class InvalidVideoError(WhiteTextError):
    """The payload is not a valid processable video."""


class VideoTooLargeError(WhiteTextError):
    """The input video exceeds configured safety limits."""


class VideoTooLongError(VideoTooLargeError):
    """The input video duration exceeds configured limits."""


class VideoProcessingError(WhiteTextError):
    """FFmpeg/ffprobe failed while processing video."""


# Backward-compatible names used by the rest of this cog.
WhitetextError = WhiteTextError


class WhitetextUserError(WhiteTextError):
    """Expected validation or processing problem caused by user input."""


class WhitetextFontError(FontNotFoundError, WhitetextUserError):
    """Required local font is missing or cannot be loaded."""


class WhitetextUnsupportedMediaError(UnsupportedMediaError, WhitetextUserError):
    """The attachment is not one of the supported media types."""


class WhitetextProcessingError(WhitetextUserError):
    """Known processing failure that should not crash the bot."""
