"""doctor's platform helpers - the parts that have to work on the Windows host.

`plan-general.md` section 11 (G0 acceptance gate) needs `doctor` to report the memory and
GPU  posture of whatever host runs it. Before the move off WSL this meant reading
`/proc/meminfo` unconditionally, which raised FileNotFoundError on Windows and turned the
gate into a crash. These tests pin the host-agnostic contract defined in
`docs/design.md` section 5: a check must never raise, and it must not assume POSIX.
"""

from __future__ import annotations

import sys

import pytest

from jobagent import cli
from jobagent.checks import CHECKS, RAM_FLOOR_GB, Check, _is_wsl, _total_ram_gb, check_memory
from jobagent.config import settings

IS_WINDOWS = sys.platform == "win32"


def test_total_ram_matches_the_host_platform() -> None:
    """The probe returns plausible physical RAM on either host."""
    gb = _total_ram_gb()
    assert 1.0 <= gb <= 4096.0, f"implausible RAM reading: {gb}"


def test_windows_host_is_not_detected_as_wsl() -> None:
    """Windows must take the host branch - never the WSL one."""
    if IS_WINDOWS:
        assert _is_wsl() is False
    else:
        assert isinstance(_is_wsl(), bool)


def test_ram_probe_does_not_reach_for_proc_on_windows() -> None:
    """Guard the regression itself: /proc/meminfo is POSIX-only."""
    if not IS_WINDOWS:
        pytest.skip("Windows-only branch")
    assert _total_ram_gb() > 0.0


def test_check_memory_reports_a_usable_verdict() -> None:
    result = check_memory(settings())
    assert isinstance(result, Check)
    assert result.status in ("OK", "WARN")
    assert "GB" in result.detail


def test_check_memory_degrades_to_warn_when_the_probe_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """design.md section 5: a check must never raise, so a dead probe is a WARN."""

    def boom() -> float:
        raise OSError("no memory information available")

    monkeypatch.setattr("jobagent.checks._total_ram_gb", boom)
    result = check_memory(settings())
    assert result.status == "WARN"
    assert "RAM probe unavailable" in result.detail


def test_ram_floor_matches_the_documented_headroom() -> None:
    """architecture.md section 7: the stage-split scheduler assumes >= 20 GB."""
    assert RAM_FLOOR_GB == 20.0


def test_checks_tuple_matches_the_documented_names() -> None:
    """design.md section 5 pins the check set and its order."""
    assert [fn.__name__ for fn in CHECKS] == [
        "check_python",
        "check_repo_files",
        "check_config",
        "check_address",
        "check_db",
        "check_memory",
        "check_gpu",
        "check_ollama",
        "check_browser",
        "check_mail",
        "check_deepseek",
        "check_budget",
    ]


def test_cli_console_survives_a_captured_stream(capsys: pytest.CaptureFixture[str]) -> None:
    """The crash was `console.print(table)` with the output captured (pipe/file).

    Rich renders tables with box-drawing glyphs; on Windows the legacy path encoded them
    with the ANSI code page. Rendering here must simply not raise on any host.
    """
    from rich.table import Table

    table = Table(title="jobagent doctor - doc set PS-5.4")
    table.add_column("check")
    table.add_column("status")
    table.add_row("memory", "OK")
    cli.console.print(table)
    assert capsys.readouterr().out


def test_make_console_disables_the_legacy_windows_path() -> None:
    """The locale's ANSI code page must never be able to mangle a table again."""
    if not IS_WINDOWS:
        pytest.skip("Windows-only guard")
    assert cli.make_console().legacy_windows is False
