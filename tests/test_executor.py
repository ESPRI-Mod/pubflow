import unittest
from unittest.mock import patch

from workflow.executor import build_publish_command
from workflow.publisher_output import (
    parse_publication_statuses,
    publisher_dataset_id,
)


class PublisherStatusParsingTests(unittest.TestCase):
    def setUp(self):
        self.datasets = [
            ("CMIP6.example.variable#20180802", "/maps/a.map", "PENDING"),
            ("CMIP6.example.variable#20181022", "/maps/b.map", "PENDING"),
            ("CMIP6.example.other#20181123", "/maps/c.map", "PENDING"),
        ]

    def test_converts_registered_version_to_publisher_version(self):
        self.assertEqual(
            publisher_dataset_id("CMIP6.example.variable#20180802"),
            "CMIP6.example.variable.v20180802",
        )

    def test_parses_pass_and_fail_and_omits_unattempted_dataset(self):
        output = """
INFO PUB_STATUS=PASS id=CMIP6.example.variable.v20180802|esgf.ipsl.fr
INFO PUB_STATUS=FAIL id=CMIP6.example.variable.v20181022|esgf.ipsl.fr
"""

        statuses, unknown = parse_publication_statuses(output, self.datasets)

        self.assertEqual(
            statuses,
            {
                "CMIP6.example.variable#20180802": "SUCCESS",
                "CMIP6.example.variable#20181022": "FAILED",
            },
        )
        self.assertNotIn("CMIP6.example.other#20181123", statuses)
        self.assertEqual(unknown, [])

    def test_reports_status_for_unknown_dataset(self):
        output = (
            "PUB_STATUS=PASS "
            "id=CMIP6.example.unknown.v20190101|esgf.ipsl.fr"
        )

        statuses, unknown = parse_publication_statuses(output, self.datasets)

        self.assertEqual(statuses, {})
        self.assertEqual(
            unknown,
            ["CMIP6.example.unknown.v20190101"],
        )

    def test_publish_command_can_force_temporary_stac_generation(self):
        with (
            patch(
                "workflow.executor.get_publisher_config",
                return_value={"executable": "esgpublish", "arguments": []},
            ),
            patch(
                "workflow.executor.get_active_esg_config",
                return_value=("test", "/tmp/esg.yaml"),
            ),
        ):
            command = build_publish_command(
                "/tmp/mapfiles",
                save_stac=True,
            )

        self.assertEqual(command.count("--save-stac"), 1)
        self.assertEqual(command[-2:], ["--map", "/tmp/mapfiles"])


if __name__ == "__main__":
    unittest.main()
