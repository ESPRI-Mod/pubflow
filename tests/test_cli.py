from unittest.mock import patch

from typer.testing import CliRunner

from pubflow.cli import app


runner = CliRunner()


def test_root_help_uses_subject_action_groups():
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for subject in (
        "campaign",
        "dataset",
        "publication",
        "archive",
        "stac",
        "grist",
        "report",
    ):
        assert subject in result.stdout

    assert "run-diagnostics" not in result.stdout
    assert "cleanup-stac-items" not in result.stdout


def test_publication_help_lists_actions():
    result = runner.invoke(app, ["publication", "--help"])

    assert result.exit_code == 0
    assert "run" in result.stdout
    assert "retry" in result.stdout
    assert "diagnose" in result.stdout


def test_version_is_0_2_0():
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "pubflow 0.2.0"


def test_legacy_command_remains_available():
    result = runner.invoke(app, ["publish", "--help"])

    assert result.exit_code == 0
    assert "--batch-size" in result.stdout


def test_publication_run_forwards_timeout_and_retry_options():
    with patch("pubflow.cli.publish_campaign") as publish:
        result = runner.invoke(
            app,
            [
                "publication", "run", "campaign",
                "--timeout-seconds", "42",
                "--no-status-retries", "3",
            ],
        )

    assert result.exit_code == 0, result.output
    publish.assert_called_once_with(
        "campaign",
        limit=None,
        batch_size=50,
        timeout_seconds=42,
        no_status_retries=3,
    )


def test_publication_dry_run_does_not_pass_execution_only_options():
    with patch("pubflow.cli.dry_run_campaign") as dry_run:
        result = runner.invoke(
            app,
            [
                "publication", "run", "campaign", "--dry-run",
                "--timeout-seconds", "42",
                "--no-status-retries", "3",
            ],
        )

    assert result.exit_code == 0, result.output
    dry_run.assert_called_once_with("campaign", limit=None, batch_size=50)
