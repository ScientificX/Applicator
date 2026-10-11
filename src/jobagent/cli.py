"""jobagent CLI - the G0 surface.

Implemented: doctor, config show, db init/status.
Everything else is a labelled stub naming the stage that will build it
(plan-general.md section 11 is the authority for stage IDs).
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from . import __version__, db
from .checks import FAIL, SKIP, WARN, run_all
from .config import CONFIG_DIR, ROOT, load_criteria, load_proxies, settings

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Autonomous multi-channel job application agent.")
config_app = typer.Typer(help="Inspect the parsed configuration.")
db_app = typer.Typer(help="Database management.")
app.add_typer(config_app, name="config")
app.add_typer(db_app, name="db")
console = Console()

STATUS_STYLE = {"OK": "green", "WARN": "yellow", "FAIL": "red", SKIP: "dim"}


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"jobagent {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True,
                                 help="Print the version and exit."),
) -> None:
    """jobagent - see README.md for the doc map and current stage."""


@app.command()
def doctor() -> None:
    """Verify the environment end to end (the G0 acceptance gate)."""
    s = settings()
    results = run_all(s)
    table = Table(title=f"jobagent doctor - doc set PS-5.4, package v{__version__}")
    table.add_column("check", style="bold")
    table.add_column("status")
    table.add_column("detail", overflow="fold", max_width=96)
    for c in results:
        table.add_row(c.name, f"[{STATUS_STYLE.get(c.status, 'white')}]{c.status}[/]", c.detail)
    console.print(table)
    fails = [c for c in results if c.status == FAIL]
    warns = [c for c in results if c.status == WARN]
    if fails:
        console.print(f"[red]{len(fails)} FAIL[/], {len(warns)} warn -> fix the FAILs before starting a stage")
        raise typer.Exit(1)
    console.print(f"[green]all checks passed[/] ({len(warns)} warning(s))")


@config_app.command("show")
def config_show(as_json: bool = typer.Option(False, "--json", help="Emit JSON.")) -> None:
    """Print the parsed criteria and the proxy posture."""
    criteria = load_criteria()
    proxies = load_proxies()
    if as_json:
        console.print_json(json.dumps({
            "criteria": criteria.model_dump(),
            "proxy_pools": len(proxies.pools),
            "config_dir": str(CONFIG_DIR),
        }))
        return
    t = criteria.target
    table = Table(title=f"criteria - {CONFIG_DIR / 'criteria.yaml'}")
    table.add_column("field", style="bold")
    table.add_column("value", overflow="fold", max_width=88)
    table.add_row("seniority", ", ".join(t.seniority))
    table.add_row("title_include", "\n".join(t.title_include))
    table.add_row("title_exclude (veto)", "\n".join(t.title_exclude))
    table.add_row("locations", f"preferred={t.locations.preferred} next={t.locations.next}")
    table.add_row("stack_must", ", ".join(t.stack_must))
    table.add_row("top_k_per_run", str(t.top_k_per_run))
    table.add_row("fresh_window_minutes", str(t.fresh_window_minutes))
    table.add_row("proxy pools", str(len(proxies.pools)) + (" (concurrency forced to 1)" if not proxies.pools else ""))
    console.print(table)


@db_app.command("init")
def db_init() -> None:
    """Create or upgrade the SQLite schema."""
    s = settings()
    s.ensure_dirs()
    conn = db.connect(s.db_path)
    version = db.migrate(conn)
    console.print(f"schema v{version} ready at {s.db_path} ({len(db.table_names(conn))} tables)")
    conn.close()


@db_app.command("status")
def db_status() -> None:
    """Show schema version and row counts."""
    s = settings()
    if not s.db_path.exists():
        console.print(f"[yellow]no database at {s.db_path}[/] -> run: jobagent db init")
        raise typer.Exit(1)
    conn = db.connect(s.db_path)
    counts = db.row_counts(conn)
    table = Table(title=f"{s.db_path} - schema v{db.schema_version(conn)}")
    table.add_column("table", style="bold")
    table.add_column("rows", justify="right")
    for name, n in counts.items():
        table.add_row(name, str(n))
    console.print(table)
    conn.close()


def _stub(stage: str) -> None:
    console.print(f"[yellow]not implemented[/] - planned for [bold]{stage}[/] "
                  f"(stage router: plan-general.md section 11)")
    raise typer.Exit(2)


@app.command()
def tenants() -> None:
    """Discover/verify ATS tenants (G1-W1, Workday doc sections 6-7)."""
    _stub("G1-W1")


@app.command()
def discover() -> None:
    """Crawl sources for postings matching criteria (G1)."""
    _stub("G1")


@app.command()
def match() -> None:
    """Filter, score and rank postings (G2)."""
    _stub("G2")


@app.command()
def tailor() -> None:
    """Build a tailored resume per posting (G2)."""
    _stub("G2")


@app.command()
def apply() -> None:
    """Submit prepared applications (G3, then G5-W2 for Workday)."""
    _stub("G3 / G5-W2")


@app.command()
def onboard() -> None:
    """Provision ATS account identities and verify them via the mail worker (G5-W1)."""
    _stub("G5-W1")


@app.command()
def run() -> None:
    """Full pipeline: hunt -> rank -> tailor -> route -> execute (G6)."""
    _stub("G6")


@app.command()
def report() -> None:
    """Funnel and cost report (G4)."""
    _stub("G4")


if __name__ == "__main__":
    app()
