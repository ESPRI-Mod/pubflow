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
from workflow.stac_cleanup import cleanup_stac_items
from workflow.validator import validate_campaign

app = typer.Typer(
    help="ESGF publication workflow manager.",
    no_args_is_help=True,
)

campaign_app = typer.Typer(help="Campaign management commands.")
dataset_app = typer.Typer(help="Dataset registration and validation commands.")
publication_app = typer.Typer(help="Dataset publication commands.")
archive_app = typer.Typer(help="Archive workflow commands.")
stac_app = typer.Typer(help="STAC item maintenance commands.")
grist_app = typer.Typer(help="Grist integration commands.")
report_app = typer.Typer(help="Reporting and export commands.")

app.add_typer(campaign_app, name="campaign")
app.add_typer(dataset_app, name="dataset")
app.add_typer(publication_app, name="publication")
app.add_typer(archive_app, name="archive")
app.add_typer(stac_app, name="stac")
app.add_typer(grist_app, name="grist")
app.add_typer(report_app, name="report")


def warn_deprecated(old_command, new_command):
    typer.echo(
        f"Warning: `{old_command}` is deprecated; use `{new_command}` instead.",
        err=True,
    )


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


@dataset_app.command("register")
def dataset_register(
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


@publication_app.command("run")
def publication_run(
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


@dataset_app.command("validate")
def dataset_validate(campaign: str, limit: int | None = None):
    """Validate datasets belonging to a campaign."""
    try:
        success = validate_campaign(campaign, limit=limit)
    except ValueError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1)

    if not success:
        raise typer.Exit(code=1)


@report_app.command("export")
def report_export(output: str = "campaign_status.csv"):
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


@archive_app.command("generate")
def archive_generate(
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


@archive_app.command("import")
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


@publication_app.command("retry")
def publication_retry(
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


@publication_app.command("diagnose")
def publication_diagnose(
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


@stac_app.command("clean")
def stac_clean(
        directory: Path = typer.Option(
            Path("."),
            "--directory",
            "-d",
            help="Directory containing dumped STAC JSON files.",
        ),
        pattern: str = typer.Option(
            "CMIP*.json",
            "--pattern",
            help="Filename pattern within that directory only.",
        ),
        delete: bool = typer.Option(
            False,
            "--delete",
            help="Delete recognized items; otherwise only preview them.",
        ),
):
    """Preview or remove publisher-generated STAC Item dumps."""
    try:
        result = cleanup_stac_items(
            directory=directory,
            pattern=pattern,
            delete=delete,
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1)

    action = "Deleted" if delete else "Found"
    typer.echo(f"Directory: {result['directory']}")
    typer.echo(f"Pattern:   {result['pattern']}")
    typer.echo(f"{action}:     {result['count']}")
    for path in result["files"][:20]:
        typer.echo(f"  {path}")
    remaining = result["count"] - 20
    if remaining > 0:
        typer.echo(f"  ... and {remaining} more")
    if not delete and result["count"]:
        typer.echo("Preview only; repeat with --delete to remove these files.")


# Hidden compatibility commands keep existing operator scripts working through
# the 0.2 release while steering new usage toward the subject/action grammar.
@app.command("register", hidden=True)
def legacy_register(
        campaign_name: str,
        register_files: bool = typer.Option(False, "--register-files"),
        batch_size: int = typer.Option(100, "--batch-size"),
):
    warn_deprecated("pubflow register", "pubflow dataset register")
    return dataset_register(campaign_name, register_files, batch_size)


@app.command("validate", hidden=True)
def legacy_validate(campaign: str, limit: int | None = None):
    warn_deprecated("pubflow validate", "pubflow dataset validate")
    return dataset_validate(campaign, limit)


@app.command("publish", hidden=True)
def legacy_publish(
        campaign: str,
        limit: int | None = None,
        batch_size: int = 50,
        dry_run: bool = False,
):
    warn_deprecated("pubflow publish", "pubflow publication run")
    return publication_run(campaign, limit, batch_size, dry_run)


@app.command("retry", hidden=True)
def legacy_retry(
        campaign: str,
        limit: int = typer.Option(None, "--limit", "-l"),
):
    warn_deprecated("pubflow retry", "pubflow publication retry")
    return publication_retry(campaign, limit)


@app.command("run-diagnostics", hidden=True)
def legacy_diagnostics(
        campaign: str,
        limit: int | None = typer.Option(None, "--limit", "-l"),
        persist_stac_item: bool = typer.Option(False, "--persist-stac-item"),
        sync_grist: bool = typer.Option(True, "--sync-grist/--no-sync-grist"),
):
    warn_deprecated(
        "pubflow run-diagnostics",
        "pubflow publication diagnose",
    )
    return publication_diagnose(
        campaign,
        limit,
        persist_stac_item,
        sync_grist,
    )


@app.command("cleanup-stac-items", hidden=True)
def legacy_stac_cleanup(
        directory: Path = typer.Option(Path("."), "--directory", "-d"),
        pattern: str = typer.Option("CMIP*.json", "--pattern"),
        delete: bool = typer.Option(False, "--delete"),
):
    warn_deprecated("pubflow cleanup-stac-items", "pubflow stac clean")
    return stac_clean(directory, pattern, delete)


@app.command("archive-import", hidden=True)
def legacy_archive_import(results_file: str):
    warn_deprecated("pubflow archive-import", "pubflow archive import")
    return archive_import(results_file)


@app.command("export", hidden=True)
def legacy_export(output: str = "campaign_status.csv"):
    warn_deprecated("pubflow export", "pubflow report export")
    return report_export(output)


@app.command()
def version():
    """Show the pubflow version."""
    typer.echo("pubflow 0.2.0")


if __name__ == "__main__":
    app()
