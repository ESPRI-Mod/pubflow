import json
import os
import tempfile
import unittest
from pathlib import Path

from workflow.stac_items import reconcile_stac_item, retained_stac_path


class StacItemRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.staging = self.root / "staging"
        self.staging.mkdir()
        self.previous_directory = os.environ.get("PUBFLOW_STAC_ITEMS_DIR")
        os.environ["PUBFLOW_STAC_ITEMS_DIR"] = str(self.root / "retained")
        self.dataset_id = "CMIP6.example.dataset#20260101"
        self.filename = "CMIP6.example.dataset.v20260101.json"

    def tearDown(self):
        if self.previous_directory is None:
            os.environ.pop("PUBFLOW_STAC_ITEMS_DIR", None)
        else:
            os.environ["PUBFLOW_STAC_ITEMS_DIR"] = self.previous_directory
        self.temp_dir.cleanup()

    def write_generated_item(self):
        source = self.staging / self.filename
        source.write_text(json.dumps({
            "type": "Feature",
            "stac_version": "1.1.0",
            "id": self.filename.removesuffix(".json"),
        }))
        return source

    def test_failed_item_is_retained(self):
        self.write_generated_item()

        action, path = reconcile_stac_item(
            self.staging,
            self.dataset_id,
            "FAILED",
        )

        self.assertEqual(action, "retained")
        self.assertEqual(Path(path), retained_stac_path(self.dataset_id))
        self.assertTrue(Path(path).is_file())

    def test_success_removes_stale_failed_item(self):
        destination = retained_stac_path(self.dataset_id)
        destination.parent.mkdir(parents=True)
        destination.write_text("stale")

        action, path = reconcile_stac_item(
            self.staging,
            self.dataset_id,
            "SUCCESS",
        )

        self.assertEqual(action, "removed")
        self.assertIsNone(path)
        self.assertFalse(destination.exists())

    def test_unreached_dataset_is_not_retained(self):
        self.write_generated_item()

        action, path = reconcile_stac_item(
            self.staging,
            self.dataset_id,
            "PENDING",
        )

        self.assertEqual(action, "ignored")
        self.assertIsNone(path)
        self.assertFalse(retained_stac_path(self.dataset_id).exists())


if __name__ == "__main__":
    unittest.main()
