import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow.stac_validation import validate_stac_item_file


class LocalStacValidationTests(unittest.TestCase):
    def validate(self, item, schemas):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "item.json"
            path.write_text(json.dumps(item))
            with (
                patch("workflow.stac_validation._validate_stac_structure"),
                patch(
                    "workflow.stac_validation._load_schema",
                    side_effect=lambda url: schemas[url],
                ),
            ):
                return validate_stac_item_file(path)

    def test_reports_json_path_and_enum_suggestion(self):
        schema_url = "https://example.test/cmip6/v2.0.2/schema.json"
        item = {
            "stac_extensions": [schema_url],
            "properties": {"cmip6:status": "publshed"},
        }
        schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "properties": {
                "properties": {
                    "properties": {
                        "cmip6:status": {
                            "enum": ["published", "retracted"],
                        }
                    }
                }
            },
        }

        result = self.validate(item, {schema_url: schema})

        self.assertFalse(result.valid)
        self.assertEqual(result.first_issue.path, "$.properties.cmip6:status")
        self.assertEqual(result.first_issue.rejected_value, "publshed")
        self.assertEqual(result.first_issue.suggested_value, "published")

    def test_validates_every_declared_extension(self):
        first = "https://example.test/first.json"
        second = "https://example.test/second.json"
        item = {"stac_extensions": [first, second], "value": 2}
        schemas = {
            first: {"type": "object", "required": ["value"]},
            second: {
                "type": "object",
                "properties": {"value": {"minimum": 1}},
            },
        }

        result = self.validate(item, schemas)

        self.assertTrue(result.valid)
        self.assertEqual(result.issues, [])

    def test_schema_download_failure_is_not_reported_as_invalid_item(self):
        item = {"stac_extensions": ["https://example.test/schema.json"]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "item.json"
            path.write_text(json.dumps(item))
            with (
                patch("workflow.stac_validation._validate_stac_structure"),
                patch(
                    "workflow.stac_validation._load_schema",
                    side_effect=RuntimeError("schema unavailable"),
                ),
            ):
                result = validate_stac_item_file(path)

        self.assertIsNone(result.valid)
        self.assertIn("schema unavailable", result.error)


if __name__ == "__main__":
    unittest.main()
