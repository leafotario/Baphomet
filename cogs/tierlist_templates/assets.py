from __future__ import annotations

import hashlib
import io
import pathlib
import tempfile

from PIL import Image, ImageOps, UnidentifiedImageError

from .models import StoredAsset, utc_now_iso


class TierListAssetError(Exception):
    pass


class TierListAssetStore:
    def __init__(
        self,
        root: str | pathlib.Path = "data/tierlist_assets",
        *,
        max_pixels: int = 25_000_000,
        max_dimension: int = 1000,
    ) -> None:
        self.root = pathlib.Path(root)
        self.max_pixels = max_pixels
        self.max_dimension = max(128, max_dimension)

    def store_image_asset(self, image_bytes: bytes) -> StoredAsset:
        normalized, width, height = self._normalize_image(image_bytes)
        digest = hashlib.sha256(normalized).hexdigest()
        relative_path = f"{digest[:2]}/{digest}.png"
        target = self._safe_path(relative_path)

        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                prefix=f"{digest}.",
                suffix=".tmp",
                dir=str(target.parent),
                delete=False,
            ) as tmp:
                tmp.write(normalized)
                tmp_path = pathlib.Path(tmp.name)
            tmp_path.replace(target)

        return StoredAsset(
            sha256=digest,
            relative_path=relative_path,
            mime_type="image/png",
            byte_size=len(normalized),
            width=width,
            height=height,
            created_at=utc_now_iso(),
        )

    def load_asset_bytes(self, asset: StoredAsset) -> bytes:
        return self.load_asset_bytes_by_path(asset.relative_path)

    def load_asset_bytes_by_path(self, relative_path: str) -> bytes:
        path = self._safe_path(relative_path)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise TierListAssetError("asset local não encontrado") from exc

    def _normalize_image(self, image_bytes: bytes) -> tuple[bytes, int, int]:
        if not image_bytes:
            raise TierListAssetError("imagem vazia")

        previous_limit = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = self.max_pixels
        try:
            with Image.open(io.BytesIO(image_bytes)) as raw:
                try:
                    raw.seek(0)
                except Exception:
                    pass
                image = raw.convert("RGBA")
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
            raise TierListAssetError("imagem inválida ou grande demais") from exc
        finally:
            Image.MAX_IMAGE_PIXELS = previous_limit

        if image.width <= 0 or image.height <= 0:
            raise TierListAssetError("imagem sem dimensões válidas")

        if max(image.size) > self.max_dimension:
            image = ImageOps.contain(
                image,
                (self.max_dimension, self.max_dimension),
                method=Image.Resampling.LANCZOS,
            )

        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        normalized = output.getvalue()
        if not normalized:
            raise TierListAssetError("falha ao normalizar imagem")
        return normalized, image.width, image.height

    def _safe_path(self, relative_path: str) -> pathlib.Path:
        candidate = (self.root / relative_path).resolve()
        root = self.root.resolve()
        if root != candidate and root not in candidate.parents:
            raise TierListAssetError("caminho de asset inválido")
        return candidate


def store_image_asset(store: TierListAssetStore, image_bytes: bytes) -> StoredAsset:
    return store.store_image_asset(image_bytes)


def load_asset_bytes(store: TierListAssetStore, asset: StoredAsset) -> bytes:
    return store.load_asset_bytes(asset)
