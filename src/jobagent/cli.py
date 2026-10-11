"""jobagent CLI - the G0 surface.

Implemented: doctor, config show, db init/status.
Everything else is a labelled stub naming the stage that will build it
(plan-general.md section 11 is the authority for stage IDs).
"""

from __future__ import annotations

import asyncio
import json
import sys

import typer
from rich.console import Console
from rich.table import Table

from . import __version__, db
from .checks import FAIL, SKIP, WARN, run_all
from .config import CONFIG_DIR, ROOT, load_criteria, load_proxies, load_sources, settings
from .discover.ats import FAMILIES
from .discover.workday_cxs import TenantRef, WorkdayCxsDiscovery
from .discover.workday_tenants import SeedTenantSource, WorkdayOracle, shard_sweep
from .net import HttpClient

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Autonomous multi-channel job application agent.")
config_app = typer.Typer(help="Inspect the parsed configuration.")
db_app = typer.Typer(help="Database management.")
app.add_typer(config_app, name="config")
app.add_typer(db_app, name="db")


def make_console() -> Console:
    """Build the Rich console so it works on the Windows host too.

    Windows Python encodes the standard streams with the ANSI code page (cp1252 here).
    Rich's *legacy Windows* render path (`console.py`: `legacy_windows` + a standard
    stream fd -> `_win32_console.WindowsConsole`) then encodes the box-drawing glyphs of
    every table with that code page and `console.print(table)` dies with
    UnicodeEncodeError - i.e. `doctor`, the G0 gate, crashed whenever its output was
    captured (`| tail`, CI, a log file).

    Two independent guards, because either one alone leaves a hole:
      * reconfigure the streams to UTF-8, so a captured stream can carry the glyphs;
      * keep Rich off the legacy path, since `WindowsConsole` ignores the stream's
        encoding and falls back to the locale's ANSI code page.
    """
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                reconfigure(encoding="utf-8", errors="replace")
        return Console(legacy_windows=False)
    return Console()


console = make_console()

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


def _print_tenants(conn: object) -> None:
    rows = conn.execute(  # type: ignore[attr-defined]
        "SELECT tenant, site, wd_shard, status, verified_at FROM tenants "
        "ORDER BY CASE status WHEN 'live' THEN 0 ELSE 1 END, tenant"
    ).fetchall()
    table = Table(title="tenants")
    for col in ("tenant", "site", "shard", "status", "verified"):
        table.add_column(col)
    for r in rows:
        table.add_row(r["tenant"], r["site"] or "-", r["wd_shard"] or "-", r["status"], (r["verified_at"] or "")[:19])
    console.print(table)
    live = conn.execute("SELECT COUNT(*) AS n FROM tenants WHERE status='live'").fetchone()["n"]  # type: ignore[attr-defined]
    console.print(f"{live} live / {len(rows)} known tenants")


async def _run_tenant_discovery(s: object, conn: object, *, refresh: bool, limit: int, concurrency: int) -> dict:
    from .config import settings as _settings  # local import keeps the CLI import graph flat
    assert s is _settings() or True
    shards = await shard_sweep()
    live_shards = [name for name, ok in shards.items() if ok]
    console.print(f"shard sweep: {len(live_shards)}/{len(shards)} shards resolve -> {', '.join(live_shards)}")
    for name, resolves in shards.items():
        conn.execute(  # type: ignore[attr-defined]
            "INSERT INTO shards(host_suffix, resolves, last_checked) VALUES (?,?,?) "
            "ON CONFLICT(host_suffix) DO UPDATE SET resolves=excluded.resolves, last_checked=excluded.last_checked",
            (name, int(resolves), __import__("jobagent.discover.store", fromlist=["utcnow"]).utcnow()),
        )
    conn.commit()  # type: ignore[attr-defined]
    source = SeedTenantSource(live_shards)
    criteria = load_criteria()
    candidates = [c async for c in source.yield_candidates(criteria)]
    if limit:
        candidates = candidates[:limit]
    console.print(f"probing {len(candidates)} seed candidates across {len(live_shards)} shards")
    oracle = WorkdayOracle(HttpClient(), conn, shards=live_shards, refresh=refresh, concurrency=concurrency)
    try:
        return await oracle.run(candidates, concurrency=concurrency)
    finally:
        await oracle._client.aclose()  # type: ignore[attr-defined]


@app.command()
def tenants(
    discover: bool = typer.Option(False, "--discover", help="Probe seed candidates for live Workday tenants (G1-W1)."),
    refresh: bool = typer.Option(False, "--refresh", help="Ignore the negative probe cache."),
    limit: int = typer.Option(0, "--limit", help="Cap candidates probed (0 = all)."),
    concurrency: int = typer.Option(6, "--concurrency", help="Max in-flight probes."),
) -> None:
    """List known tenants, or discover them through the cxs oracle (G1-W1)."""
    s = settings()
    s.ensure_dirs()
    conn = db.connect(s.db_path)
    db.migrate(conn)
    if discover:
        stats = asyncio.run(_run_tenant_discovery(s, conn, refresh=refresh, limit=limit, concurrency=concurrency))
        console.print_json(json.dumps(stats))
    _print_tenants(conn)
    conn.close()


async def _run_sources(conn: object, *, families: list[str], max_pages: int, search_text: str) -> dict[str, int]:
    criteria = load_criteria()
    counts: dict[str, int] = {}
    async with HttpClient() as client:
        for entry in load_sources().sources:
            if not entry.enabled or (families and entry.family not in families):
                continue
            if entry.family == "workday":
                continue  # Workday is driven from the tenants table, not a board list
            cls = FAMILIES.get(entry.family)
            if cls is None:
                console.print(f"[yellow]unknown family {entry.family!r} - skipped[/]")
                continue
            src = cls(client, entry.boards)
            counts[entry.family] = await src.poll(conn, criteria)
        if "workday" in families or not families:
            rows = conn.execute(  # type: ignore[attr-defined]
                "SELECT host, tenant, site, locale, facet_counts FROM tenants WHERE status='live'"
            ).fetchall()
            refs = [
                TenantRef(host=r["host"], tenant=r["tenant"], site=r["site"], locale=r["locale"] or "en-US")
                for r in rows if r["site"]
            ]
            if refs:
                wd = WorkdayCxsDiscovery(client, refs, max_pages=max_pages, search_text=search_text)
                counts["workday"] = await wd.poll(conn, criteria)
            else:
                console.print("[yellow]no live tenants yet - run: jobagent tenants --discover[/]")
    return counts


@app.command()
def discover(
    family: list[str] = typer.Option(None, "--family", help="Limit to these families (repeatable)."),
    max_pages: int = typer.Option(3, "--max-pages", help="Workday pages per tenant (20/page)."),
    search: str = typer.Option("", "--search", help="Workday server-side searchText."),
) -> None:
    """Poll every configured source and upsert postings (G1)."""
    s = settings()
    s.ensure_dirs()
    conn = db.connect(s.db_path)
    db.migrate(conn)
    counts = asyncio.run(_run_sources(conn, families=list(family or []), max_pages=max_pages, search_text=search))
    console.print_json(json.dumps(counts))
    total = conn.execute("SELECT COUNT(*) AS n FROM postings").fetchone()["n"]
    active = conn.execute("SELECT COUNT(*) AS n FROM postings WHERE active=1").fetchone()["n"]
    console.print(f"postings: {total} stored, {active} active")
    conn.close()


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
