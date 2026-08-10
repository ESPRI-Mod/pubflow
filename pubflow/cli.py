import typer
from pathlib import Path

from workflow.campaign import get_campaign
from workflow.database import connect
from workflow.registry import register_dataset
from workflow.executor import publish_campaign, dry_run_campaign

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
def version():
    """Show the pubflow version."""

    typer.echo("pubflow 0.1.0")


if __name__ == "__main__":
    app()