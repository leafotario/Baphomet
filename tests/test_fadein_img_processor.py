from __future__ import annotations

import io
import unittest

from PIL import Image, features

from cogs.fadein_img.errors import (
    AnimatedImageNotSupportedError,
    ImageTooLargeError,
    InvalidImageError,
    OutputTooLargeError,
)
from cogs.fadein_img.processor import (
    FadeInImageConfig,
    build_fadein_frames,
    open_and_validate_static_image,
    process_fadein_image_bytes,
)


def image_bytes(
    format_name: str,
    *,
    size: tuple[int, int] = (64, 48),
    color: tuple[int, int, int, int] = (30, 120, 210, 255),
) -> bytes:
    image = Image.new("RGBA", size, color)
    if format_name.upper() in {"JPEG", "JPG"}:
        image = image.convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format=format_name)
    return buffer.getvalue()


def animated_gif_bytes() -> bytes:
    frames = [
        Image.new("RGB", (64, 48), (255, 0, 0)),
        Image.new("RGB", (64, 48), (0, 0, 255)),
    ]
    buffer = io.BytesIO()
    frames[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )
    return buffer.getvalue()


def assert_valid_fadein_gif(
    test_case: unittest.TestCase,
    buffer: io.BytesIO,
    *,
    expected_color: tuple[int, int, int],
    expected_frames: int = 20,
    expected_duration_ms: int = 50,
    color_tolerance: int = 0,
) -> None:
    test_case.assertEqual(buffer.tell(), 0)
    output = Image.open(buffer)
    test_case.assertEqual(output.format, "GIF")
    test_case.assertEqual(getattr(output, "n_frames", 1), expected_frames)

    durations = []
    for index in range(expected_frames):
        output.seek(index)
        durations.append(output.info.get("duration"))
    test_case.assertEqual(durations, [expected_duration_ms] * expected_frames)
    test_case.assertEqual(sum(durations), 1000)

    output.seek(0)
    first = output.convert("RGB")
    test_case.assertEqual(first.getpixel((0, 0)), (0, 0, 0))

    output.seek(expected_frames - 1)
    last = output.convert("RGB")
    actual_color = last.getpixel((0, 0))
    for actual, expected in zip(actual_color, expected_color):
        test_case.assertLessEqual(abs(actual - expected), color_tolerance)


class FadeInImageProcessorTests(unittest.TestCase):
    def test_png_normal_generates_gif(self) -> None:
        buffer, filename = process_fadein_image_bytes(image_bytes("PNG", color=(30, 120, 210, 255)))

        self.assertEqual(filename, "fadein.gif")
        assert_valid_fadein_gif(self, buffer, expected_color=(30, 120, 210))

    def test_jpg_normal_generates_gif(self) -> None:
        buffer, filename = process_fadein_image_bytes(image_bytes("JPEG", color=(200, 40, 10, 255)))

        self.assertEqual(filename, "fadein.gif")
        assert_valid_fadein_gif(self, buffer, expected_color=(200, 40, 10), color_tolerance=2)

    @unittest.skipUnless(features.check("webp"), "Pillow build without WEBP support")
    def test_webp_static_generates_gif(self) -> None:
        buffer, filename = process_fadein_image_bytes(image_bytes("WEBP", color=(10, 180, 90, 255)))

        self.assertEqual(filename, "fadein.gif")
        output = Image.open(buffer)
        self.assertEqual(output.format, "GIF")
        self.assertEqual(getattr(output, "n_frames", 1), 20)

    def test_png_transparency_is_flattened_over_black(self) -> None:
        image = Image.new("RGBA", (64, 48), (200, 10, 10, 0))
        image.putpixel((8, 8), (255, 0, 0, 255))
        raw = io.BytesIO()
        image.save(raw, format="PNG")

        buffer, _ = process_fadein_image_bytes(raw.getvalue())
        output = Image.open(buffer)
        output.seek(getattr(output, "n_frames", 1) - 1)
        last = output.convert("RGB")

        self.assertEqual(last.getpixel((0, 0)), (0, 0, 0))
        self.assertEqual(last.getpixel((8, 8)), (255, 0, 0))

    def test_jpg_exif_orientation_is_applied(self) -> None:
        image = Image.new("RGB", (80, 40), (10, 20, 30))
        exif = Image.Exif()
        exif[274] = 6
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", exif=exif)

        normalized = open_and_validate_static_image(buffer.getvalue(), FadeInImageConfig(min_width=1, min_height=1))

        self.assertEqual(normalized.size, (40, 80))
        self.assertEqual(normalized.mode, "RGB")

    def test_animated_gif_is_rejected(self) -> None:
        with self.assertRaises(AnimatedImageNotSupportedError):
            process_fadein_image_bytes(animated_gif_bytes())

    def test_invalid_file_is_rejected(self) -> None:
        with self.assertRaises(InvalidImageError):
            process_fadein_image_bytes(b"MZ fake executable renamed to png")

    def test_large_image_is_rejected(self) -> None:
        config = FadeInImageConfig(max_width=32, max_height=32, max_pixels=32 * 32)

        with self.assertRaises(ImageTooLargeError):
            process_fadein_image_bytes(image_bytes("PNG", size=(64, 48)), config)

    def test_small_image_is_rejected(self) -> None:
        with self.assertRaises(ImageTooLargeError):
            process_fadein_image_bytes(image_bytes("PNG", size=(16, 16)))

    def test_total_pixels_limit_is_enforced(self) -> None:
        config = FadeInImageConfig(max_width=100, max_height=100, max_pixels=1000)

        with self.assertRaises(ImageTooLargeError):
            process_fadein_image_bytes(image_bytes("PNG", size=(40, 40)), config)

    def test_build_frames_first_black_and_last_original(self) -> None:
        image = Image.new("RGB", (40, 40), (11, 22, 33))
        config = FadeInImageConfig(fps=10, duration_seconds=1.0)

        frames = build_fadein_frames(image, config)

        self.assertEqual(len(frames), 10)
        self.assertEqual(frames[0].getpixel((0, 0)), (0, 0, 0))
        self.assertEqual(frames[-1].getpixel((0, 0)), (11, 22, 33))

    def test_output_limit_is_enforced(self) -> None:
        config = FadeInImageConfig(max_output_bytes=32)

        with self.assertRaises(OutputTooLargeError):
            process_fadein_image_bytes(image_bytes("PNG", size=(64, 48)), config)
