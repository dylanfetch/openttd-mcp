from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from openttd_mcp import launcher
from tests.support import TempDirTestCase, touch


class VisualLauncherFilesystemTests(TempDirTestCase):
    def test_visual_root_stages_runtime_assets_and_writes_configuration(self):
        binary = self.tmp_path / "runtime/openttd"
        data = self.tmp_path / "runtime/data"
        root = self.tmp_path / "visual"
        touch(binary)
        touch(data / "baseset/base.grf")
        touch(data / "lang/english.lng")

        with (
            mock.patch.object(launcher, "OTTD_BIN", binary),
            mock.patch.object(launcher, "OTTD_DATA", data),
            mock.patch.object(launcher, "WSLG_ROOT", root),
        ):
            run_root, config = launcher.prepare_visual_root()

        self.assertEqual(run_root, root)
        self.assertEqual(config, root / "openttd.cfg")
        config_text = config.read_text()
        self.assertIn("map_x = 6", config_text)
        self.assertIn("autosave_on_exit = false", config_text)
        self.assertEqual((root / "baseset/base.grf").read_text(), "x\n")
        self.assertEqual((root / "lang/english.lng").read_text(), "x\n")

    def test_missing_binary_stops_before_creating_a_runtime_root(self):
        root = self.tmp_path / "visual"
        with (
            mock.patch.object(launcher, "OTTD_BIN", self.tmp_path / "missing"),
            mock.patch.object(launcher, "WSLG_ROOT", root),
            self.assertRaises(SystemExit) as caught,
        ):
            launcher.prepare_visual_root()

        self.assertIn("openttd-runtime-setup.md", str(caught.exception))
        self.assertFalse(root.exists())

    def test_runtime_environment_is_isolated_and_requests_software_rendering(self):
        run_root = self.tmp_path / "run"

        env = launcher.build_env(run_root)

        self.assertEqual(env["HOME"], str(run_root / "home"))
        self.assertEqual(env["XDG_CONFIG_HOME"], str(run_root / "xdg-config"))
        self.assertEqual(env["XDG_DATA_HOME"], str(run_root / "xdg-data"))
        self.assertEqual(env["LIBGL_ALWAYS_SOFTWARE"], "1")
        self.assertEqual(env["MESA_LOADER_DRIVER_OVERRIDE"], "llvmpipe")
        self.assertTrue((run_root / "home").is_dir())

    def test_save_discovery_ignores_desync_dumps_and_snapshot_records_mtimes(self):
        run_root = self.tmp_path / "run"
        first = run_root / "save/a.sav"
        second = run_root / "save/z.sav"
        touch(first)
        touch(second)
        touch(run_root / "save/dmp_cmds_1.sav")
        touch(run_root / "save/autosave/automatic.sav")
        os.utime(first, (10, 10))
        os.utime(second, (20, 20))

        self.assertEqual(launcher.save_dir(run_root), run_root / "save")
        self.assertEqual(
            launcher.command_log_path(run_root),
            run_root / "save/autosave/commands-out.log",
        )
        os.utime(run_root / "save/autosave/automatic.sav", (30, 30))

        self.assertEqual(launcher.manual_saves(run_root), [first, second])
        # Autosaves are session evidence (exit.sav is the forget-proof final
        # save of a baseline episode), so the snapshot tracks them too.
        self.assertEqual(
            launcher.save_snapshot(run_root),
            {"a.sav": 10, "z.sav": 20, "autosave/automatic.sav": 30},
        )

    def test_episode_directory_number_follows_only_well_formed_directories(self):
        parent = self.tmp_path / "accepted-run"
        (parent / "episode-01").mkdir(parents=True)
        (parent / "episode-07").mkdir()
        (parent / "episode-final").mkdir()
        touch(parent / "episode-99")

        self.assertEqual(launcher.next_episode_dir(parent), parent / "episode-08")
        self.assertEqual(
            launcher.next_episode_dir(self.tmp_path / "empty"),
            self.tmp_path / "empty/episode-01",
        )

    def test_empty_capture_creates_no_accepted_artifact(self):
        run_root = self.tmp_path / "run"
        output = io.StringIO()
        with (
            mock.patch.object(launcher, "ROOT", self.tmp_path),
            redirect_stdout(output),
        ):
            result = launcher.capture_session(run_root, "900-capture-fixture", {})

        self.assertIsNone(result)
        self.assertFalse((self.tmp_path / "artifacts/runs").exists())

    def test_capture_copies_only_changed_saves_and_allocates_fresh_episode(self):
        run_root = self.tmp_path / "run"
        old = run_root / "save/old.sav"
        changed = run_root / "save/changed.sav"
        new = run_root / "save/new.sav"
        command_log = run_root / "save/autosave/commands-out.log"
        for path in (old, changed, new, command_log):
            touch(path, path.name)
        os.utime(old, (10, 10))
        os.utime(changed, (30, 30))
        os.utime(new, (40, 40))
        accepted = self.tmp_path / "artifacts/runs/900-capture-fixture"
        (accepted / "episode-01").mkdir(parents=True)

        with mock.patch.object(launcher, "ROOT", self.tmp_path):
            dest = launcher.capture_session(
                run_root,
                "900-capture-fixture",
                {"old.sav": 10, "changed.sav": 20},
            )

        self.assertEqual(dest, accepted / "episode-02")
        self.assertEqual(
            {path.name for path in dest.iterdir()},
            {"commands-out.log", "changed.sav", "new.sav"},
        )
        self.assertFalse((dest / "old.sav").exists())

    def test_capture_without_log_or_without_new_save_preserves_available_evidence(self):
        run_root = self.tmp_path / "run"
        save = run_root / "save/final.sav"
        touch(save, "save")
        output = io.StringIO()
        with (
            mock.patch.object(launcher, "ROOT", self.tmp_path),
            redirect_stdout(output),
        ):
            first = launcher.capture_session(run_root, "900-save-only", {})
        self.assertEqual((first / "final.sav").read_text(), "save")

        run_root_2 = self.tmp_path / "run-2"
        touch(launcher.command_log_path(run_root_2), "commands")
        output = io.StringIO()
        with (
            mock.patch.object(launcher, "ROOT", self.tmp_path),
            redirect_stdout(output),
        ):
            second = launcher.capture_session(run_root_2, "900-log-only", {})
        self.assertEqual((second / "commands-out.log").read_text(), "commands")


class VisualLauncherCommandTests(TempDirTestCase):
    def test_command_variants_distinguish_save_load_from_fresh_capture(self):
        binary = self.tmp_path / "openttd"
        save = self.tmp_path / "game.sav"
        config = self.tmp_path / "openttd.cfg"
        with mock.patch.object(launcher, "OTTD_BIN", binary):
            load = launcher.build_command(save, config)
            fresh_capture = launcher.build_command(
                save, config, log_commands=True, new_game=True
            )

        self.assertEqual(load[:3], [str(binary), "-g", str(save)])
        self.assertEqual(load[-2:], ["-v", "sdl"])
        self.assertNotIn("desync=2", load)
        self.assertNotIn(str(save), fresh_capture)
        self.assertEqual(fresh_capture[-3:], ["-d", "desync=2", "-g"])
        self.assertEqual(fresh_capture.count("-g"), 1)

    def test_missing_save_is_rejected_before_runtime_preparation(self):
        with (
            mock.patch.object(sys, "argv", ["openttd-playtest", "missing.sav"]),
            mock.patch.object(launcher, "ROOT", self.tmp_path),
            mock.patch.object(launcher, "prepare_visual_root") as prepare,
            self.assertRaises(SystemExit) as caught,
        ):
            launcher.main()

        self.assertIn("Save file does not exist", str(caught.exception))
        prepare.assert_not_called()

    def test_print_command_prepares_runtime_but_does_not_launch(self):
        run_root = self.tmp_path / "run"
        config = run_root / "openttd.cfg"
        output = io.StringIO()
        with (
            mock.patch.object(
                sys, "argv", ["openttd-playtest", "--new-game", "--print-command"]
            ),
            mock.patch.object(
                launcher, "prepare_visual_root", return_value=(run_root, config)
            ),
            mock.patch.object(launcher, "build_env", return_value={}),
            mock.patch.object(launcher.subprocess, "Popen") as popen,
            redirect_stdout(output),
        ):
            return_code = launcher.main()

        self.assertEqual(return_code, 0)
        self.assertTrue(output.getvalue().strip().endswith("-v sdl -g"))
        popen.assert_not_called()

    def test_waiting_capture_launches_once_then_returns_process_status(self):
        run_root = self.tmp_path / "run"
        run_root.mkdir()
        config = run_root / "openttd.cfg"
        process = SimpleNamespace(pid=4321, wait=mock.Mock(return_value=6))
        captured: list[tuple[Path, str, dict[str, float]]] = []

        def record_capture(root: Path, name: str, before: dict[str, float]) -> None:
            captured.append((root, name, before))

        with (
            mock.patch.object(
                sys,
                "argv",
                ["openttd-playtest", "--new-game", "--capture", "900-capture-fixture"],
            ),
            mock.patch.object(
                launcher, "prepare_visual_root", return_value=(run_root, config)
            ),
            mock.patch.object(launcher, "build_env", return_value={"ENV": "isolated"}),
            mock.patch.object(
                launcher, "save_snapshot", return_value={"prior.sav": 10.0}
            ),
            mock.patch.object(launcher, "capture_session", side_effect=record_capture),
            mock.patch.object(
                launcher.subprocess, "Popen", return_value=process
            ) as popen,
            redirect_stdout(io.StringIO()),
        ):
            return_code = launcher.main()

        self.assertEqual(return_code, 6)
        process.wait.assert_called_once_with()
        self.assertEqual(
            captured,
            [(run_root, "900-capture-fixture", {"prior.sav": 10.0})],
        )
        command = popen.call_args.args[0]
        self.assertIn("desync=2", command)
        self.assertEqual(command[-1], "-g")
        self.assertEqual(popen.call_args.kwargs["cwd"], str(run_root))
        self.assertEqual(popen.call_args.kwargs["env"], {"ENV": "isolated"})

    def test_detached_save_launch_returns_without_waiting(self):
        save = self.tmp_path / "game.sav"
        touch(save)
        run_root = self.tmp_path / "run"
        run_root.mkdir()
        config = run_root / "openttd.cfg"
        process = SimpleNamespace(pid=1234, wait=mock.Mock())

        with (
            mock.patch.object(sys, "argv", ["openttd-playtest", str(save)]),
            mock.patch.object(
                launcher, "prepare_visual_root", return_value=(run_root, config)
            ),
            mock.patch.object(launcher, "build_env", return_value={}),
            mock.patch.object(launcher.subprocess, "Popen", return_value=process),
            redirect_stdout(io.StringIO()),
        ):
            return_code = launcher.main()

        self.assertEqual(return_code, 0)
        process.wait.assert_not_called()

    def test_episode_profile_main_starts_a_seeded_new_game_and_reports_provenance(
        self,
    ):
        for seed_source, seed_arguments in (
            ("explicit", ["--seed", "123"]),
            ("generated", []),
        ):
            with self.subTest(seed_source=seed_source):
                run_root = self.tmp_path / f"episode-{seed_source}"
                run_root.mkdir()
                config = run_root / "openttd.cfg"
                session = {"horizon_target": "1995-01-01"}
                process = SimpleNamespace(pid=999, wait=mock.Mock())
                output = io.StringIO()
                with (
                    mock.patch.object(
                        sys,
                        "argv",
                        [
                            "openttd-playtest",
                            "--episode-profile",
                            "--horizon-years",
                            "5",
                            *seed_arguments,
                        ],
                    ),
                    mock.patch.object(launcher, "EPISODE_ROOT", run_root),
                    mock.patch.object(
                        launcher,
                        "prepare_episode_root",
                        return_value=(config, session),
                    ) as prepare,
                    mock.patch.object(
                        launcher.random, "randrange", return_value=456
                    ) as generate_seed,
                    mock.patch.object(launcher, "build_env", return_value={}),
                    mock.patch.object(
                        launcher.subprocess, "Popen", return_value=process
                    ) as popen,
                    redirect_stdout(output),
                ):
                    return_code = launcher.main()

                self.assertEqual(return_code, 0)
                selected_seed = prepare.call_args.args[2]
                self.assertEqual(prepare.call_args.args[:2], (run_root, 5))
                if seed_source == "explicit":
                    self.assertEqual(selected_seed, 123)
                    generate_seed.assert_not_called()
                else:
                    generate_seed.assert_called_once_with(1, 2**31)
                command = popen.call_args.args[0]
                self.assertEqual(command[-1], "-g")
                self.assertEqual(command[command.index("-G") + 1], str(selected_seed))
                self.assertIn("script=5", command)
                process.wait.assert_not_called()
                provenance = dict(
                    line.split("=", 1)
                    for line in output.getvalue().splitlines()
                    if "=" in line
                )
                self.assertEqual(provenance["SEED"], str(selected_seed))
                self.assertIn("HORIZON", provenance)
                self.assertIn("new 128x128 game", provenance["SAVE"])


if __name__ == "__main__":
    unittest.main()
