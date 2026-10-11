"""doctor checks - everything G0 is supposed to prove actually works.

Every check returns a Check(name, status, detail). Statuses: OK | WARN | FAIL | SKIP.
WARN never fails the run (e.g. a model not yet pulled); FAIL does.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import db
from .config import (
    CONFIG_DIR,
    ROOT,
    Settings,
    load_answers,
    load_criteria,
    load_proxies,
    load_tenants,
    load_visa,
)

OK, WARN, FAIL, SKIP = "OK", "WARN", "FAIL", "SKIP"
WSL_CONFIG = Path("/mnt/c/Users/favou/.wslconfig")
_HTTP = 15.0


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def check_python(s: Settings) -> Check:
    v = sys.version_info
    detail = f"{v.major}.{v.minor}.{v.micro} at {sys.executable}"
    if (v.major, v.minor) != (3, 13):
        return Check("python", WARN, detail + " (plan pins 3.13)")
    return Check("python", OK, detail)


def check_repo_files(s: Settings) -> Check:
    required = ["pyproject.toml", "uv.lock", "README.md", "plan-general.md", "plan-workday.md"]
    missing = [f for f in required if not (ROOT / f).exists()]
    if missing:
        return Check("repo files", FAIL, "missing: " + ", ".join(missing))
    return Check("repo files", OK, f"{len(required)} required files present at {ROOT}")


def check_config(s: Settings) -> Check:
    try:
        criteria = load_criteria()
        load_visa()
        load_tenants()
        load_answers()
        proxies = load_proxies()
    except Exception as exc:
        return Check("config", FAIL, f"{type(exc).__name__}: {exc}")
    t = criteria.target
    pools = len(proxies.pools)
    detail = (
        f"{len(t.title_include)} include / {len(t.title_exclude)} veto patterns; "
        f"{len(t.stack_must)} must-have skills; top_k={t.top_k_per_run}; "
        f"proxy pools: {pools}"
    )
    if pools == 0:
        detail += " -> tenant_concurrency_cap forced to 1"
    return Check("config", OK, detail)


def check_db(s: Settings) -> Check:
    try:
        s.ensure_dirs()
        conn = db.connect(s.db_path)
        db.migrate(conn)
        version = db.schema_version(conn)
        n = len(db.table_names(conn))
        conn.close()
    except Exception as exc:
        return Check("database", FAIL, f"{type(exc).__name__}: {exc}")
    return Check("database", OK, f"schema v{version}, {n} tables at {s.db_path}")


def check_memory(s: Settings) -> Check:
    total_kb = 0
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal"):
            total_kb = int(line.split()[1])
            break
    gb = total_kb / 1024 / 1024
    configured = WSL_CONFIG.exists()
    detail = f"WSL MemTotal {gb:.1f} GB; .wslconfig {'present' if configured else 'MISSING'}"
    if gb < 20 and not configured:
        return Check("memory", WARN, detail + " -> run: wsl --shutdown (from Windows) once .wslconfig is in place")
    if gb < 20:
        return Check("memory", WARN, detail + " -> .wslconfig written but not applied yet: run wsl --shutdown from Windows")
    return Check("memory", OK, detail)


def check_gpu(s: Settings) -> Check:
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,compute_cap,driver_version",
                "--format=csv,noheader",
            ],
            capture_output=True, text=True, timeout=20,
        )
        if out.returncode != 0 or not out.stdout.strip():
            raise RuntimeError(out.stderr.strip() or "no output")
    except Exception as exc:
        return Check("gpu", WARN, f"not available ({type(exc).__name__}) - local models fall back to CPU")
    line = out.stdout.strip().splitlines()[0]
    name, total, used, cc, driver = [p.strip() for p in line.split(",")]
    note = ""
    if cc.startswith("12"):
        note = " | sm_120: needs CUDA 12.8+ builds - use Ollama, not torch wheels"
    return Check("gpu", OK, f"{name} {total} ({used} used) compute {cc} driver {driver}{note}")


def check_ollama(s: Settings) -> Check:
    try:
        version = httpx.get(f"{s.ollama_base}/api/version", timeout=_HTTP).json()
        tags = httpx.get(f"{s.ollama_base}/api/tags", timeout=_HTTP).json()
    except Exception as exc:
        return Check("ollama", FAIL, f"unreachable at {s.ollama_base}: {type(exc).__name__}")
    names = {m.get("name", "") for m in tags.get("models", [])}
    def present(want: str) -> bool:
        base = want.split(":")[0]
        return any(n == want or n == want + ":latest" or n.split(":")[0] == base for n in names)
    missing = [m for m in (s.ollama_classify_model, s.ollama_embed_model, s.ollama_failover_model) if not present(m)]
    detail = f"v{version.get('version', '?')}; {len(names)} models: {', '.join(sorted(names)) or 'none'}"
    if s.ollama_embed_model in missing:
        return Check("ollama", WARN, detail + f" -> pull {s.ollama_embed_model} (G0 requirement)")
    if missing:
        return Check("ollama", WARN, detail + " -> optional missing: " + ", ".join(missing))
    return Check("ollama", OK, detail)


def check_browser(s: Settings) -> Check:
    try:
        from patchright.async_api import async_playwright
    except Exception as exc:
        return Check("browser", FAIL, f"patchright import failed: {type(exc).__name__} -> uv run patchright install chromium")

    async def launch() -> str:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            v = browser.version
            await browser.close()
            return v

    try:
        v = asyncio.run(launch())
    except Exception as exc:
        return Check("browser", FAIL, f"chromium launch failed: {type(exc).__name__}: {exc} -> uv run patchright install chromium")
    return Check("browser", OK, f"patchright chromium {v} launched headless")


def check_mail(s: Settings) -> Check:
    base = s.mail_worker_base.rstrip("/")
    try:
        anon = httpx.get(f"{base}/emails?recipient=probe%40toesk.uk", timeout=_HTTP)
    except Exception as exc:
        return Check("mail worker", FAIL, f"unreachable: {type(exc).__name__}")
    if anon.status_code != 401:
        return Check("mail worker", FAIL, f"expected 401 without a token, got {anon.status_code}")
    if not s.agent_token:
        return Check("mail worker", WARN, "reachable, auth enforced (401). AGENT_TOKEN unset -> set it to verify authenticated reads")
    try:
        auth = httpx.get(f"{base}/emails?recipient=probe%40toesk.uk", headers={"Authorization": f"Bearer {s.agent_token}"}, timeout=_HTTP)
    except Exception as exc:
        return Check("mail worker", FAIL, f"authenticated request failed: {type(exc).__name__}")
    if auth.status_code == 200:
        n = len(auth.json().get("emails", []))
        return Check("mail worker", OK, f"auth ok; {n} stored messages for probe address")
    return Check("mail worker", FAIL, f"authenticated request returned {auth.status_code}")


def check_deepseek(s: Settings) -> Check:
    if not s.deepseek_api_key:
        return Check("deepseek", WARN, "DEEPSEEK_API_KEY unset -> remote LLM stages cannot run")
    h = {"Authorization": f"Bearer {s.deepseek_api_key}"}
    try:
        models = httpx.get(f"{s.deepseek_base_url}/models", headers=h, timeout=_HTTP).json()
        ids = [m.get("id") for m in models.get("data", [])]
    except Exception as exc:
        return Check("deepseek", FAIL, f"{s.deepseek_base_url}/models failed: {type(exc).__name__}")
    if s.deepseek_model not in ids:
        return Check("deepseek", FAIL, f"model {s.deepseek_model!r} not offered; available: {', '.join(filter(None, ids))}")
    detail = f"{s.deepseek_model} available ({len(ids)} models)"
    try:
        bal = httpx.get(f"{s.deepseek_base_url}/user/balance", headers=h, timeout=_HTTP).json()
        info = (bal.get("balance_infos") or [{}])[0]
        total = float(info.get("total_balance", 0) or 0)
        detail += f"; balance ${total:.2f}"
        if total < s.jobagent_max_cost_per_day_usd:
            return Check("deepseek", WARN, detail + f" -> below one day of budget (${s.jobagent_max_cost_per_day_usd:.2f}); top up before a full run")
    except Exception:
        detail += "; balance unavailable"
    return Check("deepseek", OK, detail)


def check_budget(s: Settings) -> Check:
    per_run, per_day = s.jobagent_max_cost_per_run_usd, s.jobagent_max_cost_per_day_usd
    if per_run > per_day:
        return Check("budget", FAIL, f"per-run ${per_run:.2f} exceeds per-day ${per_day:.2f}")
    return Check("budget", OK, f"caps: ${per_run:.2f}/run, ${per_day:.2f}/day; contact address {s.jobagent_contact_address or 'UNSET'}")


def check_address(s: Settings) -> Check:
    if not s.jobagent_contact_address:
        return Check("address", FAIL, "JOBAGENT_CONTACT_ADDRESS unset - this is the address on every application")
    if "+" in s.jobagent_contact_address:
        return Check("address", FAIL, f"{s.jobagent_contact_address} uses plus-addressing, which some validators reject")
    return Check("address", OK, f"canonical contact address {s.jobagent_contact_address}")


CHECKS = (
    check_python,
    check_repo_files,
    check_config,
    check_address,
    check_db,
    check_memory,
    check_gpu,
    check_ollama,
    check_browser,
    check_mail,
    check_deepseek,
    check_budget,
)


def run_all(s: Settings) -> list[Check]:
    results: list[Check] = []
    for fn in CHECKS:
        try:
            results.append(fn(s))
        except Exception as exc:
            results.append(Check(fn.__name__, FAIL, f"check crashed: {type(exc).__name__}: {exc}"))
    return results
