import json
from pathlib import Path
from types import SimpleNamespace

import duckdb
from typer.testing import CliRunner

import pubflow.cli as cli
from workflow.registry import reconcile_campaign_datasets, register_dataset


PROJECT_ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


def make_database(path):
    conn = duckdb.connect(str(path))
    conn.execute((PROJECT_ROOT / "db" / "schema.sql").read_text())
    conn.execute(
        "INSERT INTO campaigns VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["campaign", "PROJECT", "activity", "institution", ".", None, None],
    )
    return conn


def make_generator():
    fields = [
        "project",
        "activity",
        "institution",
        "source",
        "experiment",
        "member",
        "table",
        "variable",
        "grid",
        "version",
    ]
    parts = [SimpleNamespace(source_collection=field) for field in fields]
    return SimpleNamespace(directory_specs=SimpleNamespace(parts=parts))


def write_mapfile(path, dataset_id, files):
    lines = [
        f"{dataset_id} | {file_path} | {size} | "
        f"checksum={checksum} | mod_time={mod_time}"
        for file_path, size, checksum, mod_time in files
    ]
    path.write_text("\n".join(lines) + "\n")


def test_reregistration_invalidates_statuses_when_mapfile_changes(tmp_path):
    conn = make_database(tmp_path / "registry.duckdb")
    dataset_id = "P.A.I.S.E.M.T.V.G.v1"
    old_mapfile = tmp_path / "old.map"
    new_mapfile = tmp_path / "new.map"
    write_mapfile(old_mapfile, dataset_id, [("old.nc", 1, "old", "old")])
    write_mapfile(new_mapfile, dataset_id, [("new.nc", 2, "new", "new")])

    register_dataset(
        conn,
        "campaign",
        {"project": "OLD", "activity": "old", "institution": "old"},
        old_mapfile,
        make_generator(),
        register_files=True,
    )
    conn.execute(
        """
        UPDATE datasets
        SET publication_status = 'SUCCESS',
            archive_status = 'ARCHIVED',
            archive_completed_at = TIMESTAMP '2025-01-02 03:04:05'
        WHERE dataset_id = ?
        """,
        [dataset_id],
    )

    register_dataset(
        conn,
        "campaign",
        {"project": "NEW", "activity": "new", "institution": "new"},
        new_mapfile,
        make_generator(),
        register_files=True,
    )

    row = conn.execute(
        """
        SELECT project, activity, institution, drs, mapfile,
               publication_status, archive_status, archive_completed_at
        FROM datasets WHERE dataset_id = ?
        """,
        [dataset_id],
    ).fetchone()
    assert row[:3] == ("NEW", "new", "new")
    assert json.loads(row[3])["project"] == "P"
    assert row[4] == str(new_mapfile)
    assert row[5:] == ("PENDING", "PENDING", None)
    assert conn.execute(
        "SELECT file_path, file_size, checksum, mod_time FROM files"
    ).fetchall() == [("new.nc", 2, "new", "new")]


def test_reregistration_preserves_statuses_when_mapfile_is_unchanged(tmp_path):
    conn = make_database(tmp_path / "registry-unchanged.duckdb")
    dataset_id = "P.A.I.S.E.M.T.V.G.v1"
    mapfile = tmp_path / "dataset.map"
    write_mapfile(mapfile, dataset_id, [("file.nc", 1, "sum", "time")])

    for _ in range(2):
        register_dataset(
            conn,
            "campaign",
            {"project": "P", "activity": "A", "institution": "I"},
            mapfile,
            make_generator(),
            register_files=True,
        )
        conn.execute(
            """
            UPDATE datasets
            SET publication_status = 'SUCCESS', archive_status = 'SUCCESS'
            WHERE dataset_id = ?
            """,
            [dataset_id],
        )

    assert conn.execute(
        """
        SELECT publication_status, archive_status, mapfile_checksum
        FROM datasets WHERE dataset_id = ?
        """,
        [dataset_id],
    ).fetchone()[:2] == ("SUCCESS", "SUCCESS")


def test_reconciliation_removes_stale_inventory_but_keeps_attempts(tmp_path):
    conn = make_database(tmp_path / "reconcile.duckdb")
    for dataset_id in ("current", "stale"):
        conn.execute(
            """
            INSERT INTO datasets
                (dataset_id, campaign, project, activity, institution, mapfile)
            VALUES (?, 'campaign', 'P', 'A', 'I', ?)
            """,
            [dataset_id, f"{dataset_id}.map"],
        )
        conn.execute(
            "INSERT INTO files VALUES (?, ?, 1, NULL, NULL)",
            [dataset_id, f"{dataset_id}.nc"],
        )
    conn.execute(
        "INSERT INTO publication_attempts (dataset_id, run_id) VALUES (?, ?)",
        ["stale", "historic-run"],
    )

    result = reconcile_campaign_datasets(conn, "campaign", {"current"})

    assert result == {"current": 1, "removed": 1}
    assert conn.execute("SELECT dataset_id FROM datasets").fetchall() == [
        ("current",)
    ]
    assert conn.execute("SELECT dataset_id FROM files").fetchall() == [
        ("current",)
    ]
    assert conn.execute(
        "SELECT dataset_id, run_id FROM publication_attempts"
    ).fetchall() == [("stale", "historic-run")]


def test_cli_skips_cleanup_after_failure_and_fallback_is_transactional(
        tmp_path,
        monkeypatch,
):
    database_path = tmp_path / "cli.duckdb"
    mapfile_root = tmp_path / "mapfiles"
    mapfile_root.mkdir()
    (mapfile_root / "good.map").write_text("good")
    (mapfile_root / "bad.map").write_text("bad")
    conn = make_database(database_path)
    conn.execute(
        """
        INSERT INTO datasets
            (dataset_id, campaign, project, activity, institution, mapfile)
        VALUES ('stale', 'campaign', 'P', 'A', 'I', 'stale.map')
        """
    )
    conn.close()

    monkeypatch.setattr(
        cli,
        "get_campaign",
        lambda name: {
            "project": "PROJECT",
            "activity": "activity",
            "institution": "institution",
            "mapfile_root": str(mapfile_root),
        },
    )
    monkeypatch.setattr(cli, "DrsGenerator", lambda project: object())
    monkeypatch.setattr(cli, "connect", lambda: duckdb.connect(str(database_path)))

    def fake_register(conn, campaign_name, campaign, mapfile, *args, **kwargs):
        if mapfile.name == "bad.map":
            raise ValueError("broken mapfile")
        conn.execute(
            """
            INSERT INTO datasets
                (dataset_id, campaign, project, activity, institution, mapfile)
            VALUES ('good', 'campaign', 'P', 'A', 'I', 'good.map')
            ON CONFLICT DO NOTHING
            """
        )
        return {"dataset_id": "good", "files": 0}

    monkeypatch.setattr(cli, "register_dataset", fake_register)

    result = runner.invoke(
        cli.app,
        ["dataset", "register", "campaign", "--batch-size", "2"],
    )

    assert result.exit_code == 0, result.output
    assert "Succeeded:      1" in result.output
    assert "Failed:         1" in result.output
    assert "Skipping stale dataset cleanup" in result.output
    assert "Current:        1 datasets" in result.output
    assert "Stale removed:  0 datasets" in result.output
    conn = duckdb.connect(str(database_path))
    assert set(conn.execute("SELECT dataset_id FROM datasets").fetchall()) == {
        ("good",),
        ("stale",),
    }


def test_cli_refuses_empty_inventory_reconciliation_by_default(
        tmp_path,
        monkeypatch,
):
    database_path = tmp_path / "cli-empty.duckdb"
    mapfile_root = tmp_path / "mapfiles"
    mapfile_root.mkdir()
    conn = make_database(database_path)
    conn.execute(
        """
        INSERT INTO datasets
            (dataset_id, campaign, project, activity, institution, mapfile)
        VALUES ('existing', 'campaign', 'P', 'A', 'I', 'existing.map')
        """
    )
    conn.close()

    monkeypatch.setattr(
        cli,
        "get_campaign",
        lambda name: {
            "project": "PROJECT",
            "activity": "activity",
            "institution": "institution",
            "mapfile_root": str(mapfile_root),
        },
    )
    monkeypatch.setattr(cli, "DrsGenerator", lambda project: object())
    monkeypatch.setattr(cli, "connect", lambda: duckdb.connect(str(database_path)))

    result = runner.invoke(
        cli.app,
        ["dataset", "register", "campaign"],
    )

    assert result.exit_code == 0, result.output
    assert "authoritative mapfile scan was empty" in result.output
    conn = duckdb.connect(str(database_path))
    assert conn.execute("SELECT dataset_id FROM datasets").fetchall() == [
        ("existing",),
    ]
