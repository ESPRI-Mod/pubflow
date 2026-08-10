import typer
from pathlib import Path

from workflow.campaign import get_campaign
from workflow.database import connect
from workflow.registry import register_dataset
from workflow.executor import publish_campaign, dry_run_campaign
from workflow.validator import validate_campaign
from workflow.exporter import export_campaign_status, sync_to_grist
from workflow.grist import (check_connection, list_tables,
                            get_table_columns)


app = typer.Typer(
    help="ESGF publication workflow manager.",
    no_args_is_help=True,
)


@app.command()
def register(campaign_name: str):
    """
    Register all mapfiles belonging to a campaign.
    """
    campaign = get_campaign(
        campaign_name
    )
    mapfile_root = Path(
        campaign["mapfile_root"]
    )
    if not mapfile_root.exists():
        raise typer.BadParameter(
            f"Mapfile root does not exist: {mapfile_root}"
        )
    mapfiles = list(
        mapfile_root.rglob("*.map")
    )
    typer.echo(
        f"Found {len(mapfiles)} mapfiles"
    )
    conn = connect()
    success = 0
    failed = 0

    for mapfile in mapfiles:
        try:
            result = register_dataset(
                conn,
                campaign_name,
                campaign,
                mapfile,
            )
            typer.echo(
                f"Registered {result['dataset_id']} "
                f"({result['files']} files)"
            )
            success += 1

        except Exception as exc:
            typer.echo(
                f"FAILED {mapfile}: {exc}"
            )
            failed += 1
    conn.close()
    typer.echo("")

    typer.echo(
        f"Completed: "
        f"{success} succeeded, "
        f"{failed} failed"
    )


@app.command()
def publish(
        campaign: str,
        limit: int | None = None,
        batch_size: int = 50,
        dry_run: bool = False,
):
    """
    Publish datasets belonging to a campaign.
    """

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
        typer.echo(
            f"ERROR: {exc}",
            err=True,
        )
        raise typer.Exit(code=1)
@app.command()
@app.command()
def validate(
        campaign: str,
        limit: int | None = None,
):
    """
    Validate datasets belonging to a campaign.
    """
    try:
        success = validate_campaign(
            campaign,
            limit=limit,
        )
    except ValueError as exc:
        typer.echo(
            f"ERROR: {exc}",
            err=True,
        )
        raise typer.Exit(code=1)
    if not success:
        raise typer.Exit(code=1)


@app.command()
def export(
        output: str = "campaign_status.csv",
):
    """
    Export campaign and dataset status to CSV.
    """
    try:
        export_campaign_status(output)
    except Exception as exc:
        typer.echo(
            f"ERROR: {exc}",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(
        f"Exported campaign status to {output}"
    )


grist_app = typer.Typer(
    help="Grist integration commands.",
)

app.add_typer(
    grist_app,
    name="grist",
)


@grist_app.command("tables")
def grist_tables():
    """
    List tables in the configured Grist document.
    """

    try:
        result = list_tables()
        typer.echo(result)

    except Exception as exc:
        typer.echo(
            f"ERROR: {exc}",
            err=True,
        )
        raise typer.Exit(code=1)


@grist_app.command("columns")
def grist_columns(
        table: str,
):
    """
    List columns in a Grist table.
    """

    try:
        result = get_table_columns(table)
        typer.echo(result)

    except Exception as exc:
        typer.echo(
            f"ERROR: {exc}",
            err=True,
        )
        raise typer.Exit(code=1)


@grist_app.command("sync")
def grist_sync():
    """
    Synchronise publication data with Grist.
    """

    try:
        result = sync_to_grist()

        typer.echo(
            "Grist synchronisation complete"
        )
        typer.echo(
            f"Campaigns: {result['campaigns']}"
        )
        typer.echo(
            f"Datasets:  {result['datasets']}"
        )
        typer.echo(
            f"Failures:  {result['failures']}"
        )

    except Exception as exc:
        typer.echo(
            f"ERROR: {exc}",
            err=True,
        )
        raise typer.Exit(code=1)


@grist_app.command("check")
def grist_check():
    """
    Check connectivity to the configured Grist document.
    """

    try:
        check_connection()
        typer.echo(
            "Grist connection successful."
        )

    except Exception as exc:
        typer.echo(
            f"ERROR: {exc}",
            err=True,
        )
        raise typer.Exit(code=1)
@app.command()
def version():
    """Show the pubflow version."""

    typer.echo("pubflow 0.1.0")


if __name__ == "__main__":
    app()