"""Reload a save and report its independently observed OpenTTD date."""

from __future__ import annotations

import argparse
from pathlib import Path

from openttd_mcp.admin import AdminClient, AdminLifecycleConfig, allocate_admin_port
from openttd_mcp.runtime import ROOT
from openttd_mcp.verify._scaffold import (
    DEDICATED_DRIVER,
    create_run_root,
    emit_result,
    launch_openttd,
    teardown,
)
from openttd_mcp.verify._squirrel import compose_info_nut

VERIFIER_MODULE = "openttd_mcp.verify.save_date"
SAVE_DATE_TIMEOUT_SEC = 60

INFO_NUT = compose_info_nut(
    short_name="SDAT",
    description="Independent saved-date verifier.",
    date="2026-08-18",
)

MAIN_NUT = """class McpBridge extends AIController
{
    function Start()
    {
        while (true) AIController.Sleep(100);
    }
}
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("save_path")
    args = parser.parse_args(argv)
    save_path = Path(args.save_path)
    if not save_path.is_absolute():
        save_path = (ROOT / save_path).resolve()
    admin_config = AdminLifecycleConfig(
        port=allocate_admin_port(), client_name="save-date-verifier"
    )
    game_port = allocate_admin_port()
    while game_port == admin_config.port:
        game_port = allocate_admin_port()
    root, log_path, result_path, env = create_run_root(
        prefix="save-date",
        game_start_script="echo save-date verifier: retaining saved pause state\n",
        info_nut=INFO_NUT,
        main_nut=MAIN_NUT,
        log_filename="verify_save_date.log",
        admin_config=admin_config,
    )
    proc = launch_openttd(root, save_path, env, port=game_port, driver=DEDICATED_DRIVER)
    admin = AdminClient(admin_config)
    try:
        admin.connect(timeout=SAVE_DATE_TIMEOUT_SEC, register_console=False)
        date_days = admin.poll_date(timeout=SAVE_DATE_TIMEOUT_SEC)
        emit_result(
            proc,
            save_path=save_path,
            log_path=log_path,
            result_path=result_path,
            result={"ok": True, "date_days": date_days},
            verifier_module=VERIFIER_MODULE,
            driver=DEDICATED_DRIVER,
        )
        return 0
    finally:
        admin.close()
        teardown(proc, driver=DEDICATED_DRIVER)


if __name__ == "__main__":
    raise SystemExit(main())
