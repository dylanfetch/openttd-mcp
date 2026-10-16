"""Human baseline episode mode of the playtest launcher (147-human-baseline).

Defects these tests catch: a baseline session silently playing the 64x64
visual-config world instead of the shared play-episode profile, a horizon
GameScript staged with the wrong (or unsubstituted) pause year, a map seed
that is applied but not recorded (irreproducible evidence), and a capture
that drops the forget-proof exit autosave or the session provenance.
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

from openttd_mcp import launcher
from tests.support import TempDirTestCase, touch


class BaselineEpisodePreparationTests(TempDirTestCase):
    def prepare(self, horizon_years: int = 3, seed: int = 147001):
        binary = self.tmp_path / "runtime/openttd"
        data = self.tmp_path / "runtime/data"
        root = self.tmp_path / "episode"
        touch(binary)
        touch(data / "baseset/base.grf")
        touch(data / "lang/english.lng")
        with (
            mock.patch.object(launcher, "OTTD_BIN", binary),
            mock.patch.object(launcher, "OTTD_DATA", data),
        ):
            cfg, session = launcher.prepare_episode_root(root, horizon_years, seed)
        return root, cfg, session

    def test_episode_profile_generates_the_shared_play_episode_world(self):
        _root, cfg, _session = self.prepare()

        text = cfg.read_text()
        self.assertIn("map_x = 7", text)
        self.assertIn("custom_town_number = 16", text)
        self.assertIn("starting_year = 1990", text)
        # GUI adaptations: autosaves on, and a pause that forbids all company
        # actions so the horizon pause is a hard stop.
        self.assertIn("autosave_interval = 10", text)
        self.assertIn("autosave_on_exit = true", text)
        self.assertIn("command_pause_level = 0", text)
        self.assertIn("[game_scripts]", text)
        self.assertIn("EpisodeHorizon", text)

    def test_horizon_gamescript_is_staged_with_the_substituted_pause_year(self):
        root, _, session = self.prepare(horizon_years=3, seed=42)

        main_nut = (root / "game/EpisodeHorizon/main.nut").read_text()
        self.assertIn("GSDate.GetDate(1993, 1, 1)", main_nut)
        self.assertNotIn("__TARGET_YEAR__", main_nut)
        self.assertTrue((root / "game/EpisodeHorizon/info.nut").exists())
        self.assertEqual(session["horizon_target"], "1993-01-01")

    def test_session_metadata_records_seed_and_profile_for_the_evidence_bundle(self):
        root, _, session = self.prepare(horizon_years=2, seed=987654)

        persisted = json.loads((root / "session.json").read_text())
        self.assertEqual(persisted, session)
        self.assertEqual(persisted["seed"], 987654)
        self.assertEqual(persisted["map_log2"], 7)
        self.assertEqual(persisted["num_towns"], 16)
        self.assertEqual(persisted["starting_year"], 1990)
        self.assertEqual(persisted["horizon_target"], "1992-01-01")

    def test_missing_binary_stops_before_creating_an_episode_root(self):
        root = self.tmp_path / "episode"
        with (
            mock.patch.object(launcher, "OTTD_BIN", self.tmp_path / "missing"),
            self.assertRaises(SystemExit),
        ):
            launcher.prepare_episode_root(root, 3, 1)
        self.assertFalse(root.exists())


class BaselineEpisodeCommandTests(TempDirTestCase):
    def test_seed_and_null_driver_are_applied_to_the_launch_command(self):
        binary = self.tmp_path / "openttd"
        config = self.tmp_path / "openttd.cfg"
        with mock.patch.object(launcher, "OTTD_BIN", binary):
            cmd = launcher.build_command(
                self.tmp_path / "unused.sav",
                config,
                log_commands=True,
                new_game=True,
                seed=147001,
                video="null:ticks=100",
                script_debug=True,
            )

        self.assertIn("null:ticks=100", cmd)
        seed_at = cmd.index("-G")
        self.assertEqual(cmd[seed_at + 1], "147001")
        debug_at = cmd.index("-d")
        self.assertEqual(cmd[debug_at + 1], "desync=2,script=5")
        self.assertEqual(cmd[-1], "-g")


class BaselineEpisodeCaptureTests(TempDirTestCase):
    def test_capture_preserves_exit_autosave_and_session_provenance(self):
        run_root = self.tmp_path / "run"
        touch(run_root / "save/final-1992.sav", "manual")
        touch(run_root / "save/autosave/exit.sav", "exit")
        touch(run_root / "save/autosave/commands-out.log", "commands")
        touch(run_root / "session.json", "{}")
        touch(run_root / "openttd.log", "log")

        with (
            mock.patch.object(launcher, "ROOT", self.tmp_path),
            redirect_stdout(io.StringIO()),
        ):
            dest = launcher.capture_session(run_root, "900-baseline-fixture", {})

        self.assertEqual(
            {p.name for p in dest.iterdir()},
            {
                "commands-out.log",
                "final-1992.sav",
                "autosave-exit.sav",
                "session.json",
                "openttd.log",
            },
        )
        self.assertEqual((dest / "autosave-exit.sav").read_text(), "exit")


if __name__ == "__main__":
    unittest.main()
