import json
import unittest

from workflow.diagnostic_parser import parse_server_diagnostic


class DiagnosticParserTests(unittest.TestCase):
    def test_extracts_stac_enum_failure_and_suggestion(self):
        body = {
            "status_code": 400,
            "type": "https://esgf.io/publication/errors/stac-validation",
            "title": "Your request is invalid",
            "detail": (
                "Item `CMIP6.example.v1` failed validation against "
                "`https://example.test/cmip6/v2.0.2/schema.json`: "
                "'atmosphere_optical_thickness_due_to_nitrate_ambient_aerosol' "
                "is not one of ['air_temperature', "
                "'atmosphere_optical_thickness_due_to_nitrate_ambient_aerosol_particles']"
            ),
            "instance": "request-id:event-id",
        }
        output = (
            "2026-08-21 ERROR Failed to publish: Error 400: "
            + json.dumps(body)
            + "\nPUB_STATUS=FAIL id=CMIP6.example.v1|esgf.example"
        )

        result = parse_server_diagnostic(output)

        self.assertEqual(result.http_status, 400)
        self.assertEqual(
            result.schema_url,
            "https://example.test/cmip6/v2.0.2/schema.json",
        )
        self.assertEqual(
            result.rejected_value,
            "atmosphere_optical_thickness_due_to_nitrate_ambient_aerosol",
        )
        self.assertEqual(
            result.suggested_value,
            "atmosphere_optical_thickness_due_to_nitrate_ambient_aerosol_particles",
        )
        self.assertEqual(result.server_instance, "request-id:event-id")
        self.assertNotIn("air_temperature", result.summary)

    def test_handles_unstructured_failure(self):
        result = parse_server_diagnostic("ERROR Failed to publish: Error 500")

        self.assertIsNone(result.http_status)
        self.assertIn("without a structured", result.summary)


if __name__ == "__main__":
    unittest.main()
