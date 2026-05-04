from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from cogs.tierlist_templates.assets import TierListAssetStore


class TierListTemplateAssetTests(unittest.TestCase):
    def test_store_image_asset_deduplicates_local_png(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TierListAssetStore(Path(tmp))
            image = Image.new("RGBA", (64, 32), (255, 0, 0, 255))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            payload = buffer.getvalue()

            first = store.store_image_asset(payload)
            second = store.store_image_asset(payload)

            self.assertEqual(first.sha256, second.sha256)
            self.assertEqual(first.relative_path, second.relative_path)
            self.assertFalse(first.relative_path.startswith("http"))
            self.assertTrue((Path(tmp) / first.relative_path).exists())
            self.assertGreater(len(store.load_asset_bytes(first)), 0)


if __name__ == "__main__":
    unittest.main()
