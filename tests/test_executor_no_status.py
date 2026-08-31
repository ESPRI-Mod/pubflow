import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import ANY, call, patch

import duckdb

import workflow.database as database
from workflow.executor import publish_batch
from workflow.executor import publish_campaign, record_no_status_attempt
from workflow.result import PublicationResult


class NoStatusHandlingTests(unittest.TestCase):
    def test_none_stdout_is_recorded_and_dataset_remains_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "publications.duckdb"
            mapfile = root / "dataset.map"
            mapfile.write_text(
                "dataset-a | /data/example.nc | 1\n"
            )
            log_file = root / "run.log"
            log_file.write_text("")
            schema = (
                Path(__file__).resolve().parents[1] / "db" / "schema.sql"
            ).read_text()
            conn = duckdb.connect(str(database_path))
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
                VALUES ('dataset-a', 'test', 'CMIP6', 'CMIP', 'IPSL', ?,
                        'PENDING')
                """,
                [str(mapfile)],
            )
            conn.close()

            previous_database_path = database.DB_PATH
            database.DB_PATH = database_path
            completed = subprocess.CompletedProcess(
                ["esgpublish"],
                returncode=1,
                stdout=None,
            )
            try:
                with (
                    patch(
                        "workflow.executor.get_mapfile_path_mappings",
                        return_value=[],
                    ),
                    patch(
                        "workflow.executor.build_publish_command",
                        return_value=["esgpublish", "--map", "/tmp/maps"],
                    ),
                    patch(
                        "workflow.executor.subprocess.run",
                        return_value=completed,
                    ),
                ):
                    results = publish_batch(
                        [("dataset-a", str(mapfile), "PENDING")],
                        "run-none",
                        log_file,
                        "1-isolation",
                        record_missing_status=True,
                    )
            finally:
                database.DB_PATH = previous_database_path

            conn = duckdb.connect(str(database_path))
            status = conn.execute(
                "SELECT publication_status FROM datasets "
                "WHERE dataset_id = 'dataset-a'"
            ).fetchone()[0]
            attempt_status = conn.execute(
                "SELECT status FROM publication_attempts "
                "WHERE dataset_id = 'dataset-a'"
            ).fetchone()[0]
            conn.close()
            log_text = log_file.read_text()

        self.assertEqual(results, [])
        self.assertEqual(status, "PENDING")
        self.assertEqual(attempt_status, "NO_STATUS")
        self.assertIn("Publisher output length: 0", log_text)

    def test_no_status_attempt_preserves_pending_dataset(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "publications.duckdb"
            schema = (
                Path(__file__).resolve().parents[1] / "db" / "schema.sql"
            ).read_text()
            conn = duckdb.connect(str(database_path))
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
                VALUES ('dataset-a', 'test', 'CMIP6', 'CMIP', 'IPSL',
                        '/maps/a.map', 'PENDING')
                """
            )

            record_no_status_attempt(
                conn,
                "dataset-a",
                "run-1",
                1,
                "/logs/run.log",
                "No PUB_STATUS",
            )
            conn.commit()

            dataset_status = conn.execute(
                "SELECT publication_status FROM datasets "
                "WHERE dataset_id = 'dataset-a'"
            ).fetchone()[0]
            attempt = conn.execute(
                "SELECT status, error_message FROM publication_attempts "
                "WHERE dataset_id = 'dataset-a'"
            ).fetchone()
            conn.close()

        self.assertEqual(dataset_status, "PENDING")
        self.assertEqual(attempt, ("NO_STATUS", "No PUB_STATUS"))

    def test_campaign_defers_statusless_dataset_and_continues(self):
        dataset_a = ("dataset-a", "/maps/a.map", "PENDING")
        dataset_b = ("dataset-b", "/maps/b.map", "PENDING")
        success = PublicationResult(
            dataset_id="dataset-b",
            status="SUCCESS",
            exit_code=0,
            log_file="/logs/run.log",
        )

        with tempfile.TemporaryDirectory() as directory:
            log_file = Path(directory) / "run.log"
            with (
                patch(
                    "workflow.executor.get_run_log_file",
                    return_value=log_file,
                ),
                patch(
                    "workflow.executor.get_active_esg_config",
                    return_value=("test", "/tmp/esg.yaml"),
                ),
                patch(
                    "workflow.executor.get_mapfile_path_mappings",
                    return_value=[],
                ),
                patch(
                    "workflow.executor.get_campaign_datasets",
                    side_effect=[[dataset_a, dataset_b], [dataset_b], []],
                ) as select_mock,
                patch(
                    "workflow.executor.publish_batch",
                    side_effect=[[], [], [success]],
                ) as publish_mock,
                patch(
                    "workflow.executor.trigger_grist_sync",
                    return_value=Path(directory) / "grist.log",
                ),
                redirect_stdout(io.StringIO()) as output,
            ):
                publish_campaign("test", batch_size=2)

        self.assertIn("DEFERRED dataset-a", output.getvalue())
        self.assertIn("SUCCESS dataset-b", output.getvalue())
        self.assertEqual(
            publish_mock.call_args_list[1],
            call(
                [dataset_a],
                ANY,
                log_file,
                "1-isolation",
                record_missing_status=True,
            ),
        )
        self.assertEqual(
            select_mock.call_args_list[1].kwargs["exclude_dataset_ids"],
            {"dataset-a"},
        )


if __name__ == "__main__":
    unittest.main()
