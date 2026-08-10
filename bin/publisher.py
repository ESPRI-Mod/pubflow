#!/usr/bin/env python3

from pathlib import Path

import typer

from workflow.database import connect
from workflow.campaign import get_campaign, load_campaigns
from workflow.registry import register_dataset
from workflow.reporting import (list_campaigns, campaign_summary,
                                publication_summary, failed_publications)
from workflow.executor import dry_run_campaign, publish_campaign
from workflow.validator import validate_campaign
from workflow.exporter import (export_campaign_status,
                               export_campaign_summary, export_failures,
                               sync_to_grist)
from workflow.grist import check_connection, list_tables, get_table_columns

app = typer.Typer(
    help="ESGF publication workflow manager"
)


@app.command()
def validate(campaign: str,
             limit: int | None = None):
    try:
        success = validate_campaign(campaign, limit)
    except ValueError as exc:
        typer.echo(f"ERROR: {exc}",
                   err=True)
        raise typer.Exit(code=1)
    if not success:
        raise typer.Exit(code=1)


@app.command()
def campaigns_list():
    """
    List registered campaigns.
    """

    for row in list_campaigns():
        typer.echo(
            f"{row[0]} "
            f"{row[1]} "
            f"{row[2]} "
            f"{row[3]}"
        )

        @app.command()
        def status():

            """
            Show workflow status.
            """

            typer.echo("\nDatasets by campaign:\n")

            for row in campaign_summary():
                typer.echo(
                    f"{row[0]:20} {row[1]}"
                )

            typer.echo("\nPublication status:\n")

            for row in publication_summary():
                typer.echo(
                    f"{row[0]:20} {row[1]}"
                )


@app.command()
def status():
    """
    Show workflow status.
    """

    typer.echo("\nDatasets by campaign:\n")

    for row in campaign_summary():
        typer.echo(
            f"{row[0]:20} {row[1]}"
        )

    typer.echo("\nPublication status:\n")

    for row in publication_summary():
        typer.echo(
            f"{row[0]:20} {row[1]}"
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
                mapfile
            )

            typer.echo(
                f"Registered {result['dataset_id']} "
                f"({result['files']} files)"
            )

            success += 1


        except Exception as e:

            typer.echo(
                f"FAILED {mapfile}: {e}"
            )

            failed += 1

    conn.close()
    typer.echo("")
    typer.echo(
        f"Completed: {success} succeeded, {failed} failed"
    )


@app.command()
def campaigns(action: str):
    """
    Manage campaigns.
    """
    if action == "load":
        load_campaigns()
        typer.echo("Campaigns loaded")
    else:
        raise typer.BadParameter(f"Unknown action: {action}")


@app.command()
def failures():
    """
    Show failed publications.
    """

    rows = failed_publications()

    if not rows:
        typer.echo(
            "No failures"
        )

        return

    for row in rows:
        typer.echo(
            f"{row[0]} {row[1]} {row[2]}"
        )


@app.command()
def db_check():
    conn = connect()

    tables = conn.execute(
        """
        SHOW TABLES
        """
    ).fetchall()

    for table in tables:
        typer.echo(table)

    conn.close()


@app.command()
def publish(
        campaign: str,
        dry_run: bool = False,
        limit: int | None = None,
        batch_size: int = 50,
):
    if dry_run:
        dry_run_campaign(
            campaign,
            limit,
            batch_size,
        )

        return

    publish_campaign(
        campaign,
        limit,
        batch_size
    )


@app.command()
def export(output: str = "campaign_status.csv",
           export_type: str = typer.Option(
               "datasets",
               "--type"
           ),
           campaign: str | None = typer.Option(None, "--campaign")):
    if export_type == "datasets":
        export_campaign_status(output)
    elif export_type == "campaigns":
        export_campaign_summary(output)
    elif export_type == "failures":
        export_failures(output, campaign)
    else:
        typer.echo(f"ERROR: Unknonw export type {export_type}", err=True)
        raise typer.Exit(code=1)
    typer.echo(
        f"Exported {export_type} to {output}"
    )


@app.command()
def grist_check():
    try:
        check_connection()
        typer.echo("Grist connection successful")
    except Exception as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1)


@app.command()
def grist_tables():
    try:
        result = list_tables()

        for table in result.get("tables", []):
            typer.echo(
                table["id"]
            )

    except Exception as exc:
        typer.echo(
            f"ERROR: {exc}",
            err=True,
        )
        raise typer.Exit(code=1)


@app.command()
def grist_columns(table_id: str):
    try:
        result = get_table_columns(table_id)
        typer.echo(repr(result))
    except Exception as exc:
        typer.echo(
            f"ERROR: {exc}",
            err=True,
        )
        raise typer.Exit(code=1)


@app.command()
def grist_sync():
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

if __name__ == "__main__":
    app()
