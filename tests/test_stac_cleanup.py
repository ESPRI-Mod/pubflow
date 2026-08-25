import json
import tempfile
import unittest
from pathlib import Path

from workflow.stac_cleanup import cleanup_stac_items


class StacCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.item_id = "CMIP6.example.dataset.v20260101"
        self.item = self.root / f"{self.item_id}.json"
        self.item.write_text(json.dumps({
            "type": "Feature",
            "stac_version": "1.1.0",
            "stac_extensions": ["https://example.test/schema.json"],
            "id": self.item_id,
        }))
        self.unrelated = self.root / "CMIP6.settings.json"
        self.unrelated.write_text(json.dumps({"id": "settings"}))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_preview_does_not_delete_anything(self):
        result = cleanup_stac_items(self.root)

        self.assertEqual(result["count"], 1)
        self.assertTrue(self.item.exists())
        self.assertTrue(self.unrelated.exists())

    def test_delete_removes_only_recognized_stac_items(self):
        result = cleanup_stac_items(self.root, delete=True)

        self.assertEqual(result["deleted"], 1)
        self.assertFalse(self.item.exists())
        self.assertTrue(self.unrelated.exists())

    def test_pattern_cannot_escape_selected_directory(self):
        with self.assertRaises(ValueError):
            cleanup_stac_items(self.root, pattern="../*.json")


if __name__ == "__main__":
    unittest.main()
