#!/usr/bin/env python3

from pathlib import Path

import typer

from workflow.database import connect
from workflow.campaign import get_campaign, load_campaigns
from workflow.registry import register_dataset
from workflow.reporting import (list_campaigns, campaign_summary,
                                publication_summary, failed_publications)
from workflow.executor import dry_run_campaign, publish_campaign

app = typer.Typer(
    help="ESGF publication workflow manager"
)


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


if __name__ == "__main__":
    app()
