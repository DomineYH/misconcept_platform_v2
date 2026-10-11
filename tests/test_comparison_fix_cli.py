"""Cleanup failure remains a machine-readable CLI result."""

import json


def test_cleanup_cli_reports_unexpected_database_exception(monkeypatch, capsys):
    from src.cli import cleanup_comparisons as cli

    def unavailable(*args, **kwargs):
        raise RuntimeError("PRIVATE_DATABASE_FAILURE")

    monkeypatch.setattr(cli, "create_async_engine", unavailable)
    monkeypatch.setattr(
        "sys.argv", ["cleanup", "--database", "unused.db", "--execute"]
    )
    assert cli.main() == 1
    output = capsys.readouterr()
    summary = json.loads(output.out)
    assert summary["failed"] == summary["exit_status"] == 1
    assert summary["deleted"] == 0 and summary["mode"] == "execute"
    assert "PRIVATE_DATABASE_FAILURE" not in output.out
