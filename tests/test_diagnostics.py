import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

import workflow.database as database
from workflow.diagnostics import _diagnostic_command, _run_one


class DiagnosticExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database_path = self.root / "publications.duckdb"
        self.mapfile = self.root / "dataset.map"
        self.mapfile.write_text(
            "CMIP6.example.variable#20180802 | /data/example.nc | 1\n"
        )
        schema = (
            Path(__file__).resolve().parents[1] / "db" / "schema.sql"
        ).read_text()
        conn = duckdb.connect(str(self.database_path))
        conn.execute(schema)
        conn.execute(
            """
            INSERT INTO campaigns
            (name, project, activity, institution, mapfile_root)
            VALUES ('test', 'CMIP6', 'CMIP', 'IPSL', '/maps')
            """
        )
        conn.execute(
            """
            INSERT INTO datasets
            (dataset_id, campaign, project, activity, institution, mapfile,
             publication_status)
            VALUES (?, 'test', 'CMIP6', 'CMIP', 'IPSL', ?, 'FAILED')
            """,
            [self.dataset_id, str(self.mapfile)],
        )
        conn.close()
        self.original_database_path = database.DB_PATH
        database.DB_PATH = self.database_path

    def tearDown(self):
        database.DB_PATH = self.original_database_path
        self.temp_dir.cleanup()

    @property
    def dataset_id(self):
        return "CMIP6.example.variable#20180802"

    def run_attempt(self, output, returncode):
        completed = subprocess.CompletedProcess(
            args=["esgpublish"],
            returncode=returncode,
            stdout=output,
        )
        dataset = (self.dataset_id, str(self.mapfile), "FAILED")
        run_dir = self.root / "logs"
        run_dir.mkdir()
        with (
            patch(
                "workflow.diagnostics.build_publish_command",
                return_value=["esgpublish", "--map", str(self.mapfile)],
            ),
            patch(
                "workflow.executor.get_mapfile_path_mappings",
                return_value=[],
            ),
            patch("workflow.diagnostics.subprocess.run", return_value=completed),
        ):
            return _run_one(
                dataset,
                "test",
                "diagnostics_test_run",
                run_dir,
                persist_stac_item=False,
            )

    def dataset_status(self):
        conn = duckdb.connect(str(self.database_path))
        try:
            return conn.execute(
                "SELECT publication_status FROM datasets WHERE dataset_id = ?",
                [self.dataset_id],
            ).fetchone()[0]
        finally:
            conn.close()

    def test_pass_recovers_failed_dataset(self):
        row = self.run_attempt(
            "INFO PUB_STATUS=PASS "
            "id=CMIP6.example.variable.v20180802|esgf.example\n",
            0,
        )

        self.assertEqual(row["outcome"], "RECOVERED")
        self.assertEqual(self.dataset_status(), "SUCCESS")

    def test_unrecognized_output_does_not_change_failed_status(self):
        row = self.run_attempt("publisher crashed before status\n", 1)

        self.assertEqual(row["outcome"], "EXECUTION_ERROR")
        self.assertEqual(self.dataset_status(), "FAILED")

    def test_diagnostic_command_always_generates_stac(self):
        with patch(
            "workflow.diagnostics.build_publish_command",
            return_value=["esgpublish", "--map", "/tmp/example.map"],
        ):
            command = _diagnostic_command("/tmp/example.map")

        self.assertIn("--verbose", command)
        self.assertIn("--save-stac", command)


if __name__ == "__main__":
    unittest.main()
