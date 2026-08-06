#!/usr/bin/env python3

from pathlib import Path

import typer

from workflow.database import connect
from workflow.campaign import get_campaign, load_campaigns
from workflow.registry import register_dataset

app = typer.Typer(
    help="ESGF publication workflow manager"
)


@app.command()
def campaigns(
        action: str
):
    """
    Manage campaigns.
    """

    if action == "load":

        load_campaigns()

        typer.echo(
            "Campaigns loaded"
        )

    else:

        raise typer.BadParameter(
            f"Unknown action: {action}"
        )


@app.command()
def register(
        campaign_name: str
):
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
def status():
    """
    Show registration/publication status summary.
    """

    conn = connect()

    rows = conn.execute(
        """
        SELECT campaign,
               publication_status,
               COUNT(*)

        FROM datasets

        GROUP BY campaign,
                 publication_status

        ORDER BY campaign,
                 publication_status
        """
    ).fetchall()

    for row in rows:
        typer.echo(row)

    conn.close()


@app.command()
def campaigns(
        action: str
):
    """
    Manage campaigns.
    """

    if action == "load":

        load_campaigns()

        typer.echo(
            "Campaigns loaded"
        )

    else:

        raise typer.BadParameter(
            f"Unknown action: {action}"
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


if __name__ == "__main__":
    app()
