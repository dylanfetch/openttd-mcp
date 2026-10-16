"""Independent saved-date verifier contract."""

from __future__ import annotations

from pathlib import Path

from openttd_mcp.admin import UPDATE_DATE
from openttd_mcp.verify import save_date
from tests.support import FakeProc
from tests.test_admin_client import FakeAdminServer, date_push


def test_save_date_verifier_retains_pause_and_persists_the_wire_date(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}
    log_path = tmp_path / "verify.log"
    result_path = tmp_path / "verify_result.json"
    proc = FakeProc()
    expected_date = 712345
    server = FakeAdminServer(
        poll_handler=lambda update_type, _ident: (
            [date_push(expected_date)] if update_type == UPDATE_DATE else []
        )
    )

    def create_run_root(**kwargs):
        captured.update(kwargs)
        return tmp_path, log_path, result_path, {}

    def emit_result(_proc, **kwargs):
        captured["result"] = kwargs["result"]

    monkeypatch.setattr(save_date, "create_run_root", create_run_root)
    ports = iter([server.port, server.port + 1])
    monkeypatch.setattr(save_date, "allocate_admin_port", lambda: next(ports))
    monkeypatch.setattr(
        save_date,
        "AdminLifecycleConfig",
        lambda **_kwargs: server.config(),
    )

    def launch(*args, **kwargs):
        captured["launch"] = kwargs
        return proc

    monkeypatch.setattr(save_date, "launch_openttd", launch)
    monkeypatch.setattr(save_date, "emit_result", emit_result)
    monkeypatch.setattr(save_date, "teardown", lambda *args, **kwargs: None)

    exit_code = save_date.main([str(tmp_path / "final.sav")])
    server.join(timeout=2)

    assert exit_code == 0
    assert "unpause" not in str(captured["game_start_script"]).splitlines()
    assert captured["result"] == {"ok": True, "date_days": expected_date}
    assert captured["launch"] == {
        "port": server.port + 1,
        "driver": "dedicated",
    }
    assert server.errors == []
