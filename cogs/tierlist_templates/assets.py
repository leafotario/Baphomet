from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import pathlib
import uuid
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from PIL import Image, UnidentifiedImageError, features

from .exceptions import AssetValidationError, UnsupportedImageTypeError
from .models import TierAsset

if TYPE_CHECKING:
    from .asset_repository import TierAssetRepository


LOGGER = logging.getLogger("baphomet.tierlist_templates.assets")


@dataclass(frozen=True)
class ProcessedImageAsset:
    data: bytes
    asset_hash: str
    extension: str
    mime_type: str
    width: int
    height: int
    size_bytes: int
    original_format: str | None


@dataclass(frozen=True)
class StoredTemplateAsset:
    asset: TierAsset
    asset_hash: str
    storage_path: str
    width: int
    height: int
    size_bytes: int
    mime_type: str
    created_new_file: bool

    @property
    def asset_id(self) -> str:
        return self.asset.id


class TierTemplateAssetStore:
    def __init__(
        self,
        *,
        repository: TierAssetRepository,
        root_dir: str | pathlib.Path = "data/assets/tier_templates",
        max_input_bytes: int = 8 * 1024 * 1024,
        max_output_bytes: int = 6 * 1024 * 1024,
        max_dimension: int = 2048,
        max_pixels: int = 25_000_000,
        webp_quality: int = 88,
        webp_method: int = 4,
    ) -> None:
        self.repository = repository
        self.root_dir = pathlib.Path(root_dir)
        self.max_input_bytes = int(max_input_bytes)
        self.max_output_bytes = int(max_output_bytes)
        self.max_dimension = int(max_dimension)
        self.max_pixels = int(max_pixels)
        self.webp_quality = int(webp_quality)
        self.webp_method = int(webp_method)

    async def store_image_bytes(
        self,
        raw_bytes: bytes,
        *,
        source_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StoredTemplateAsset:
        processed = await asyncio.to_thread(self._process_image_sync, raw_bytes)
        existing = await self.repository.get_asset_by_hash(processed.asset_hash)
        relative_path = (
            pathlib.PurePosixPath(existing.storage_path)
            if existing is not None
            else self._relative_path_for_hash(processed.asset_hash, processed.extension)
        )
        target_path = self.root_dir / relative_path

        created_new_file = False
        if not target_path.exists():
            await asyncio.to_thread(self._write_file_atomically, target_path, processed.data)
            created_new_file = True

        asset = existing
        if asset is None:
            asset = await self.repository.create_asset(
                asset_hash=processed.asset_hash,
                storage_path=relative_path.as_posix(),
                mime_type=processed.mime_type,
                width=processed.width,
                height=processed.height,
                size_bytes=processed.size_bytes,
                source_type=source_type,
                metadata={
                    **(metadata or {}),
                    "original_format": processed.original_format,
                    "optimized_format": processed.extension.lstrip(".").upper(),
                },
            )
        return StoredTemplateAsset(
            asset=asset,
            asset_hash=processed.asset_hash,
            storage_path=asset.storage_path,
            width=asset.width,
            height=asset.height,
            size_bytes=asset.size_bytes,
            mime_type=asset.mime_type,
            created_new_file=created_new_file,
        )

    def asset_path(self, asset: TierAsset | StoredTemplateAsset) -> pathlib.Path:
        storage_path = asset.storage_path
        path = self.root_dir / storage_path
        try:
            path.resolve().relative_to(self.root_dir.resolve())
        except ValueError as exc:
            raise AssetValidationError(
                "O caminho do asset salvo é inválido.",
                detail=f"storage_path fora da raiz: {storage_path}",
                code="asset_storage_path_invalid",
            ) from exc
        return path

    async def load_asset_bytes(self, asset: TierAsset | StoredTemplateAsset) -> bytes:
        path = self.asset_path(asset)
        return await asyncio.to_thread(path.read_bytes)

    def _process_image_sync(self, raw_bytes: bytes) -> ProcessedImageAsset:
        if not raw_bytes:
            raise AssetValidationError(
                "A imagem veio vazia.",
                detail="Payload de imagem vazio.",
                code="image_empty_payload",
            )
        if len(raw_bytes) > self.max_input_bytes:
            raise AssetValidationError(
                "Essa imagem é grande demais para processar com segurança.",
                detail=f"Payload excede {self.max_input_bytes} bytes.",
                code="image_input_too_large",
            )

        original_format: str | None = None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw_bytes)) as probe:
                    original_format = probe.format
                    if original_format == "SVG":
                        raise UnsupportedImageTypeError(
                            "SVG não é aceito como imagem de template nesta etapa.",
                            detail="Pillow identificou SVG.",
                            code="image_svg_unsupported",
                        )
                    probe.verify()

                with Image.open(io.BytesIO(raw_bytes)) as image:
                    try:
                        image.seek(0)
                    except EOFError:
                        pass
                    image.load()
                    width, height = image.size
                    if width <= 0 or height <= 0:
                        raise AssetValidationError(
                            "A imagem tem dimensões inválidas.",
                            detail=f"Dimensões inválidas: {width}x{height}",
                            code="image_invalid_dimensions",
                        )
                    if width * height > self.max_pixels:
                        raise AssetValidationError(
                            "Essa imagem tem resolução grande demais para processar com segurança.",
                            detail=f"Imagem excede {self.max_pixels} pixels: {width}x{height}",
                            code="image_pixel_limit",
                        )

                    normalized = self._normalize_mode(image)
                    normalized.thumbnail((self.max_dimension, self.max_dimension), Image.Resampling.LANCZOS)
                    output_format, extension, mime_type = self._output_format()
                    output = io.BytesIO()
                    if output_format == "WEBP":
                        normalized.save(
                            output,
                            format="WEBP",
                            quality=self.webp_quality,
                            method=self.webp_method,
                            exact=normalized.mode == "RGBA",
                        )
                    else:
                        normalized.save(output, format="PNG", optimize=True)
        except Image.DecompressionBombError as exc:
            raise AssetValidationError(
                "Essa imagem é grande demais para processar com segurança.",
                detail="Pillow levantou DecompressionBombError.",
                code="image_decompression_bomb",
            ) from exc
        except Image.DecompressionBombWarning as exc:
            raise AssetValidationError(
                "Essa imagem é grande demais para processar com segurança.",
                detail="Pillow levantou DecompressionBombWarning.",
                code="image_decompression_bomb_warning",
            ) from exc
        except UnidentifiedImageError as exc:
            raise UnsupportedImageTypeError(
                "Não reconheci esse arquivo como uma imagem válida.",
                detail="Pillow não identificou o formato.",
                code="image_unidentified",
            ) from exc
        except (OSError, ValueError) as exc:
            raise AssetValidationError(
                "Não consegui validar essa imagem com segurança.",
                detail=str(exc),
                code="image_validation_failed",
            ) from exc

        optimized = output.getvalue()
        if len(optimized) > self.max_output_bytes:
            raise AssetValidationError(
                "A imagem otimizada ficou grande demais para salvar.",
                detail=f"Arquivo otimizado excede {self.max_output_bytes} bytes.",
                code="image_output_too_large",
            )
        digest = hashlib.sha256(optimized).hexdigest()
        return ProcessedImageAsset(
            data=optimized,
            asset_hash=digest,
            extension=extension,
            mime_type=mime_type,
            width=normalized.width,
            height=normalized.height,
            size_bytes=len(optimized),
            original_format=original_format,
        )

    def _normalize_mode(self, image: Image.Image) -> Image.Image:
        has_alpha = (
            image.mode in {"RGBA", "LA"}
            or (image.mode == "P" and "transparency" in image.info)
        )
        if has_alpha:
            return image.convert("RGBA")
        return image.convert("RGB")

    def _output_format(self) -> tuple[str, str, str]:
        if features.check("webp"):
            return "WEBP", ".webp", "image/webp"
        LOGGER.warning("Pillow sem suporte WEBP; usando PNG como fallback para assets de templates.")
        return "PNG", ".png", "image/png"

    def _relative_path_for_hash(self, asset_hash: str, extension: str) -> pathlib.PurePosixPath:
        return pathlib.PurePosixPath(asset_hash[:2], asset_hash[2:4], f"{asset_hash}{extension}")

    def _write_file_atomically(self, target_path: pathlib.Path, data: bytes) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = target_path.with_name(f".{target_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp_path.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target_path)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
