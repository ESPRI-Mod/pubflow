import time
from pathlib import Path

import typer
from esgvoc.apps.drs.generator import DrsGenerator
from tqdm import tqdm

from workflow.archive import generate_archive_tasks, import_archive_results
from workflow.campaign import get_campaign, load_campaigns
from workflow.database import connect
from workflow.diagnostics import run_diagnostics
from workflow.executor import dry_run_campaign, publish_campaign, retry_campaign
from workflow.exporter import export_campaign_status, sync_to_grist
from workflow.grist import check_connection, get_table_columns, list_tables
from workflow.registry import register_dataset
from workflow.validator import validate_campaign

app = typer.Typer(
    help="ESGF publication workflow manager.",
    no_args_is_help=True,
)

campaign_app = typer.Typer(help="Campaign management commands.")
grist_app = typer.Typer(help="Grist integration commands.")

app.add_typer(campaign_app, name="campaign")
app.add_typer(grist_app, name="grist")


def format_duration(seconds):
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"

    return f"{minutes}:{seconds:02d}"


@campaign_app.command("load")
def campaign_load():
    """Load campaigns from campaigns.yml into DuckDB."""
    try:
        load_campaigns()
        typer.echo("Campaigns loaded successfully")
    except Exception as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1)


@app.command()
def register(
        campaign_name: str,
        register_files: bool = typer.Option(
            False,
            "--register-files",
            help="Also store file-level metadata.",
        ),
        batch_size: int = typer.Option(
            100,
            help="Mapfiles per database transaction.",
        ),
):
    """Register all mapfiles belonging to a campaign."""
    campaign = get_campaign(campaign_name)
    mapfile_root = Path(campaign["mapfile_root"])

    if not mapfile_root.exists():
        raise typer.BadParameter(
            f"Mapfile root does not exist: {mapfile_root}"
        )

    mapfiles = list(mapfile_root.rglob("*.map"))
    typer.echo(f"Found {len(mapfiles)} mapfiles")

    drs_generator = DrsGenerator(campaign["project"].lower())
    conn = connect()

    success = 0
    failed = 0
    start_total = time.monotonic()

    with tqdm(
            total=len(mapfiles),
            desc="Registering",
            unit="mapfile",
    ) as progress:
        for start in range(0, len(mapfiles), batch_size):
            batch = mapfiles[start:start + batch_size]
            conn.execute("BEGIN")

            try:
                for mapfile in batch:
                    register_dataset(
                        conn,
                        campaign_name,
                        campaign,
                        mapfile,
                        drs_generator,
                        register_files=register_files,
                    )
                    success += 1
                    progress.update(1)

                conn.execute("COMMIT")

            except Exception:
                conn.execute("ROLLBACK")

                for mapfile in batch:
                    try:
                        register_dataset(
                            conn,
                            campaign_name,
                            campaign,
                            mapfile,
                            drs_generator,
                            register_files=register_files,
                        )
                        conn.execute("COMMIT")
                        success += 1

                    except Exception as exc:
                        conn.execute("ROLLBACK")
                        failed += 1
                        typer.echo(f"FAILED {mapfile}: {exc}")

                    progress.update(1)

    conn.close()

    elapsed = time.monotonic() - start_total
    rate = success / elapsed if elapsed else 0

    typer.echo("")
    typer.echo("Registration complete")
    typer.echo(f"  Succeeded:      {success}")
    typer.echo(f"  Failed:         {failed}")
    typer.echo(f"  Duration:       {format_duration(elapsed)}")
    typer.echo(f"  Rate:           {rate:.2f} mapfiles/s")
    typer.echo(
        f"  File metadata:  {'enabled' if register_files else 'disabled'}"
    )


@app.command()
def publish(
        campaign: str,
        limit: int | None = None,
        batch_size: int = 50,
        dry_run: bool = False,
):
    """Publish datasets belonging to a campaign."""
    try:
        if dry_run:
            dry_run_campaign(
                campaign,
                limit=limit,
                batch_size=batch_size,
            )
        else:
            publish_campaign(
                campaign,
                limit=limit,
                batch_size=batch_size,
            )

    except ValueError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1)


@app.command()
def validate(campaign: str, limit: int | None = None):
    """Validate datasets belonging to a campaign."""
    try:
        success = validate_campaign(campaign, limit=limit)
    except ValueError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1)

    if not success:
        raise typer.Exit(code=1)


@app.command()
def export(output: str = "campaign_status.csv"):
    """Export campaign and dataset status to CSV."""
    try:
        export_campaign_status(output)
        typer.echo(f"Exported campaign status to {output}")
    except Exception as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1)


@grist_app.command("tables")
def grist_tables():
    """List tables in the configured Grist document."""
    try:
        typer.echo(list_tables())
    except Exception as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1)


@grist_app.command("columns")
def grist_columns(table: str):
    """List columns in a Grist table."""
    try:
        typer.echo(get_table_columns(table))
    except Exception as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1)


@grist_app.command("sync")
def grist_sync():
    """Synchronise publication data with Grist."""
    try:
        result = sync_to_grist()
        typer.echo("Grist synchronisation complete")
        typer.echo(f"Campaigns: {result['campaigns']}")
        typer.echo(f"Datasets:  {result['datasets']}")
        typer.echo(f"Failures:  {result['failures']}")
    except Exception as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1)


@grist_app.command("check")
def grist_check():
    """Check connectivity to the configured Grist document."""
    try:
        check_connection()
        typer.echo("Grist connection successful.")
    except Exception as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1)


@app.command()
def archive(
        campaign_name: str,
        limit: int | None = typer.Option(None),
        output: str = typer.Option("archive_tasks.csv"),
):
    """Generate archive tasks for successfully published datasets."""
    try:
        count = generate_archive_tasks(
            campaign_name,
            output=output,
            limit=limit,
        )
        typer.echo(f"Generated {count} archive tasks")
        typer.echo(f"Output: {output}")
    except Exception as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1)


@app.command("archive-import")
def archive_import(results_file: str):
    """Import archive results into DuckDB."""
    try:
        result = import_archive_results(results_file)
        typer.echo(f"Success:          {result['SUCCESS']}")
        typer.echo(f"Already exists:   {result['ALREADY_EXISTS']}")
        typer.echo(f"Conflicts:        {result['CONFLICT']}")
        typer.echo(f"Failed:            {result['FAILED']}")
        typer.echo(f"Unknown datasets: {result['UNKNOWN_DATASET']}")
        typer.echo(f"Unknown statuses: {result['UNKNOWN']}")
    except Exception as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1)


@app.command("retry")
def retry(
        campaign: str,
        limit: int = typer.Option(
            None,
            "--limit",
            "-l",
        ),
):
    """Reset failed datasets to PENDING for retry."""
    try:
        count = retry_campaign(campaign, limit=limit)
        typer.echo(f"Reset {count} failed datasets "
            f"to PENDING.")
    except Exception as exc:
        typer.echo(f"ERROR: {exc}", err=True,)
        raise typer.Exit(code=1)


@app.command("run-diagnostics")
def run_diagnostics_command(
        campaign: str,
        limit: int | None = typer.Option(None, "--limit", "-l"),
        persist_stac_item: bool = typer.Option(
            False,
            "--persist-stac-item",
            help=(
                "Retain generated STAC JSON after diagnostics. "
                "It is generated temporarily regardless."
            ),
        ),
        sync_grist: bool = typer.Option(
            True,
            "--sync-grist/--no-sync-grist",
            help="Synchronize results to the Grist Diagnostics table.",
        ),
):
    """Re-run failed datasets individually and collect server diagnostics."""
    typer.echo(
        "Diagnostics performs real publication attempts. "
        "Recovered datasets will be marked SUCCESS."
    )
    try:
        result = run_diagnostics(
            campaign,
            limit=limit,
            persist_stac_item=persist_stac_item,
            sync_grist=sync_grist,
        )
    except Exception as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1)

    typer.echo("")
    typer.echo("Diagnostic run complete")
    typer.echo(f"  Selected:          {result['selected']}")
    typer.echo(f"  Recovered:         {result['recovered']}")
    typer.echo(f"  Diagnosed:         {result['diagnosed']}")
    typer.echo(f"  Unclassified:      {result['unclassified']}")
    typer.echo(f"  Execution errors:  {result['execution_errors']}")
    typer.echo(f"  Output:            {result['output_directory']}")
    typer.echo(f"  CSV:               {result['csv_file']}")
    if result["grist_synced"]:
        typer.echo("  Grist:             synchronized")
    elif result["grist_error"]:
        typer.echo(f"  Grist warning:     {result['grist_error']}")

@app.command()
def version():
    """Show the pubflow version."""
    typer.echo("pubflow 0.1.0")


if __name__ == "__main__":
    app()
