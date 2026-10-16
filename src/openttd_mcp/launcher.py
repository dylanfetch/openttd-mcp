#!/usr/bin/env python3
"""Launch an OpenTTD save in WSLg with a software, non-OpenGL SDL video path.

The normal OpenTTD GUI auto-detection can try an OpenGL/GPU renderer first under
WSLg, which emits noisy "no GPU available"/Mesa diagnostics even when the game
then falls back and opens. This launcher uses the extracted project runtime,
selects the plain SDL (non-OpenGL) video driver so OpenTTD never probes the WSLg
GPU, and redirects OpenTTD's own output to a log file so the launching terminal
stays quiet regardless of any residual diagnostics (e.g. the "AI no longer
available" notice printed when a save's AI scripts are not staged here).
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
from pathlib import Path

from openttd_mcp.lifecycle import write_openttd_config
from openttd_mcp.runtime import (
    OTTD_BIN,
    OTTD_DATA,
    ROOT,
    build_runtime_env,
    symlink_or_copy,
)

DEFAULT_SAVE = ROOT / "artifacts" / "llm_map_observation_episode.sav"
WSLG_ROOT = ROOT / ".local" / "wslg-launch" / "openttd-root"

# Human baseline sessions (--episode-profile) play the same world regime as the
# game-time play episodes: a 128x128 map with 16 towns and a 1990 start year.
# They use a separate runtime root so baseline saves never mix with
# visual-playtest saves.
EPISODE_ROOT = ROOT / ".local" / "wslg-launch" / "openttd-episode-root"
EPISODE_MAP_LOG2 = 7
EPISODE_TOWNS = 16
EPISODE_STARTING_YEAR = 1990
GS_SRC = Path(__file__).resolve().parent / "squirrel" / "episode_horizon_gs"


def write_visual_config(run_root: Path) -> Path:
    cfg = run_root / "openttd.cfg"
    # map_x/map_y are log2 sizes, so 6 => 64. Only new games (-g with no file)
    # read these; loading a save takes the map size from the save, so this is a
    # no-op there. 64x64 matches the project's default milestone map size.
    cfg.write_text(
        """[misc]
display_opt = SHOW_TOWN_NAMES|SHOW_STATION_NAMES|SHOW_SIGNS|FULL_ANIMATION|FULL_DETAIL|WAYPOINTS

[game_creation]
map_x = 6
map_y = 6

[network]
server_name = wslg-visual-launch
client_name = wslg-visual-launch
server_advertise = false

[gui]
autosave_interval = 0
autosave_on_exit = false
autosave_on_network_disconnect = false
"""
    )
    return cfg


def prepare_visual_root() -> tuple[Path, Path]:
    if not OTTD_BIN.exists():
        raise SystemExit(
            f"Missing OpenTTD binary at {OTTD_BIN}; see docs/setup/openttd-runtime-setup.md"
        )
    WSLG_ROOT.mkdir(parents=True, exist_ok=True)
    symlink_or_copy(OTTD_DATA / "baseset", WSLG_ROOT / "baseset")
    symlink_or_copy(OTTD_DATA / "lang", WSLG_ROOT / "lang")
    return WSLG_ROOT, write_visual_config(WSLG_ROOT)


def write_episode_config(run_root: Path) -> Path:
    """Write the shared play-episode generation profile, adapted for a human GUI.

    The map/terrain/economy profile comes from the same
    ``lifecycle.write_openttd_config`` the model episodes use, so parity holds by
    construction. Three GUI-session adaptations are then applied to the written
    file (each must match exactly, so a lifecycle profile change is noticed here
    instead of silently un-adapted): interval autosaves plus autosave-on-exit as
    a forget-proof final save, a command pause level that forbids every company
    action while paused (the horizon GameScript's pause is then a hard stop),
    and the auto-start entry for that GameScript.
    """
    cfg = write_openttd_config(
        run_root,
        EPISODE_MAP_LOG2,
        num_towns=EPISODE_TOWNS,
        starting_year=EPISODE_STARTING_YEAR,
    )
    text = cfg.read_text()
    for old, new in (
        ("autosave_interval = 0", "autosave_interval = 10"),
        ("autosave_on_exit = false", "autosave_on_exit = true"),
    ):
        if old not in text:
            raise RuntimeError(
                f"lifecycle.write_openttd_config no longer writes {old!r}; "
                "reconcile the episode-profile autosave adaptation"
            )
        text = text.replace(old, new)
    text += (
        "\n[construction]\n"
        "command_pause_level = 0\n"
        "\n[game_scripts]\n"
        "EpisodeHorizon = \n"
    )
    cfg.write_text(text)
    return cfg


def stage_horizon_gs(run_root: Path, target_year: int) -> Path:
    """Stage the EpisodeHorizon GameScript with the pause year substituted."""
    dst = run_root / "game" / "EpisodeHorizon"
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(GS_SRC / "info.nut", dst / "info.nut")
    template = (GS_SRC / "main.nut.template").read_text()
    (dst / "main.nut").write_text(template.replace("__TARGET_YEAR__", str(target_year)))
    return dst


def prepare_episode_root(
    run_root: Path, horizon_years: int, seed: int
) -> tuple[Path, dict[str, object]]:
    """Prepare a human baseline episode root: parity config, horizon GS, metadata.

    Returns the config path and the session metadata dict; the metadata is also
    persisted as ``session.json`` in the run root so a capture preserves the
    seed, profile, and horizon that produced the evidence.
    """
    if not OTTD_BIN.exists():
        raise SystemExit(
            f"Missing OpenTTD binary at {OTTD_BIN}; see docs/setup/openttd-runtime-setup.md"
        )
    run_root.mkdir(parents=True, exist_ok=True)
    symlink_or_copy(OTTD_DATA / "baseset", run_root / "baseset")
    symlink_or_copy(OTTD_DATA / "lang", run_root / "lang")
    cfg = write_episode_config(run_root)
    target_year = EPISODE_STARTING_YEAR + horizon_years
    stage_horizon_gs(run_root, target_year)
    session: dict[str, object] = {
        "kind": "human-baseline-episode",
        "seed": seed,
        "map_log2": EPISODE_MAP_LOG2,
        "num_towns": EPISODE_TOWNS,
        "starting_year": EPISODE_STARTING_YEAR,
        "horizon_years": horizon_years,
        "horizon_target": f"{target_year}-01-01",
    }
    (run_root / "session.json").write_text(json.dumps(session, indent=2) + "\n")
    return cfg, session


def build_env(run_root: Path) -> dict[str, str]:
    # "-v sdl" already avoids OpenTTD's OpenGL video path; these vars only take
    # effect if some GL is touched, forcing Mesa's software (llvmpipe) renderer
    # rather than probing a (commonly absent) WSLg GPU.
    return build_runtime_env(
        run_root,
        extra={
            "LIBGL_ALWAYS_SOFTWARE": "1",
            "MESA_LOADER_DRIVER_OVERRIDE": "llvmpipe",
        },
    )


def save_dir(run_root: Path) -> Path:
    # Where in-game manual saves land. Passing `-c <run_root>/openttd.cfg` makes
    # OpenTTD treat the config's directory (run_root) as its personal data dir,
    # so saves go to run_root/save/ — NOT the XDG_DATA_HOME path from the env.
    return run_root / "save"


def command_log_path(run_root: Path) -> Path:
    # OpenTTD writes the desync command log under the autosave dir of the save
    # folder.
    return save_dir(run_root) / "autosave" / "commands-out.log"


def manual_saves(run_root: Path) -> list[Path]:
    # Top-level manual saves only; skip the incidental dmp_cmds_*.sav desync
    # dumps. Autosaves are handled separately by session_saves().
    return sorted(
        p for p in save_dir(run_root).glob("*.sav") if not p.name.startswith("dmp_cmds")
    )


def session_saves(run_root: Path) -> list[Path]:
    """All capturable saves: manual saves plus autosaves (exit.sav, autosaveN).

    Autosaves matter for baseline episodes: autosave_on_exit produces the
    forget-proof final save, and interval autosaves are the crash backstop.
    """
    autosaves = sorted(
        p
        for p in (save_dir(run_root) / "autosave").glob("*.sav")
        if not p.name.startswith("dmp_cmds")
    )
    return manual_saves(run_root) + autosaves


def save_snapshot(run_root: Path) -> dict[str, float]:
    """Record {relative name: mtime} of saves present before a launch.

    The runtime root is reused across launches, so saves accumulate there. We
    snapshot the pre-existing set so capture copies only saves this session
    created or modified — never leftovers from an earlier episode.
    """
    base = save_dir(run_root)
    return {
        str(p.relative_to(base)): p.stat().st_mtime for p in session_saves(run_root)
    }


def next_episode_dir(parent: Path) -> Path:
    """Return the next unused episode-NN subdir under an accepted run dir.

    Each capture goes in its own subdir so replaying never overwrites a prior
    episode's log or saves.
    """
    existing = [
        int(p.name.split("-", 1)[1])
        for p in parent.glob("episode-*")
        if p.is_dir() and p.name.split("-", 1)[1].isdigit()
    ]
    return parent / f"episode-{(max(existing) + 1) if existing else 1:02d}"


def capture_session(
    run_root: Path, run_name: str, before: dict[str, float]
) -> Path | None:
    """Copy this session's command log and new saves into a fresh episode subdir.

    Called after OpenTTD exits, so the command log is complete and any saves the
    player made are present. `before` is the pre-launch save snapshot; only saves
    created or modified during this session are copied.
    """
    log = command_log_path(run_root)
    base = save_dir(run_root)
    new_saves = [
        p
        for p in session_saves(run_root)
        if p.stat().st_mtime > before.get(str(p.relative_to(base)), 0.0)
    ]

    if not log.exists() and not new_saves:
        # Nothing to capture — don't leave an empty episode dir behind.
        print(f"WARNING: nothing to capture (no log at {log}, no new saves). Skipping.")
        return None

    parent = ROOT / "artifacts" / "runs" / run_name
    parent.mkdir(parents=True, exist_ok=True)
    dest = next_episode_dir(parent)
    dest.mkdir()

    if log.exists():
        shutil.copy2(log, dest / "commands-out.log")
        print(f"CAPTURED_LOG={dest / 'commands-out.log'}")
    else:
        print(f"WARNING: no command log at {log} (did you pass --log-commands?)")

    for sav in new_saves:
        # Autosaves keep an autosave- prefix so the boundary/manual saves stay
        # distinguishable from the backstop copies in the flat episode dir.
        name = sav.name if sav.parent == base else f"autosave-{sav.name}"
        shutil.copy2(sav, dest / name)
        print(f"CAPTURED_SAVE={dest / name}")
    if not new_saves:
        print(
            "NOTE: no new saves this session; save in-game before quitting to capture a final save"
        )

    # Baseline-episode provenance and the horizon marker travel with the saves.
    for extra in (run_root / "session.json", run_root / "openttd.log"):
        if extra.exists():
            shutil.copy2(extra, dest / extra.name)
            print(f"CAPTURED_META={dest / extra.name}")
    return dest


def build_command(
    save_path: Path,
    cfg_path: Path,
    log_commands: bool = False,
    new_game: bool = False,
    seed: int | None = None,
    video: str = "sdl",
    script_debug: bool = False,
) -> list[str]:
    cmd = [str(OTTD_BIN)]
    if not new_game:
        # Load the given save.
        cmd += ["-g", str(save_path)]
    cmd += [
        "-c",
        str(cfg_path),
        "-x",
        "-I",
        "OpenGFX",
        "-S",
        "NoSound",
        "-M",
        "NoMusic",
        "-s",
        "null",
        "-m",
        "null",
        "-v",
        video,
    ]
    if seed is not None:
        # Pin the new-game generation seed so the map is recorded/reproducible.
        cmd += ["-G", str(seed)]
    debug_parts = []
    if log_commands:
        # desync=2 logs every player command (each internal OpenTTD Cmd*) with
        # its parameters and tick to commands-out.log, and periodically dumps
        # dmp_cmds_*.sav. This is the human-playtest capture surface: every line
        # is one legal atomic action.
        debug_parts.append("desync=2")
    if script_debug:
        # Surface GSLog output (the EpisodeHorizon boundary marker) in the log.
        debug_parts.append("script=5")
    if debug_parts:
        cmd += ["-d", ",".join(debug_parts)]
    if new_game:
        # `-g` with no filename starts a fresh game immediately (size from the
        # [game_creation] config). Placed last so nothing is parsed as its
        # optional savegame argument.
        cmd += ["-g"]
    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "save",
        nargs="?",
        default=str(DEFAULT_SAVE),
        help="Save file to load (ignored with --new-game)",
    )
    parser.add_argument(
        "--new-game",
        action="store_true",
        help="Start a fresh 64x64 game instead of loading a save. Use this for a "
        "human playtest so the command log captures a from-scratch playthrough "
        "(the default save is a pre-built LLM world).",
    )
    parser.add_argument(
        "--print-command",
        action="store_true",
        help="Print the command and exit without launching",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Wait for OpenTTD to exit instead of returning after launch",
    )
    parser.add_argument(
        "--log-commands",
        action="store_true",
        help="Enable -d desync=2 so every player command is captured to commands-out.log (human-playtest primitive capture)",
    )
    parser.add_argument(
        "--capture",
        metavar="RUN_NAME",
        help="After OpenTTD exits, copy commands-out.log and this session's saves into a "
        "fresh artifacts/runs/RUN_NAME/episode-NN/ subdir. "
        "Each run gets its own subdir, so replaying never overwrites a prior episode. "
        "Implies --wait and --log-commands.",
    )
    parser.add_argument(
        "--episode-profile",
        action="store_true",
        help="Human baseline episode: new game on the shared play-episode profile "
        "(128x128, 16 towns, 1990 start), with the EpisodeHorizon GameScript pausing "
        "the game permanently at the game-time horizon, interval + on-exit autosaves, "
        "and a recorded seed. Implies --new-game; uses a separate runtime root.",
    )
    parser.add_argument(
        "--horizon-years",
        type=int,
        default=3,
        metavar="N",
        help="Game-time horizon in years for --episode-profile (default 3; the game "
        "pauses on 1 Jan of start year + N)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        metavar="SEED",
        help="Map generation seed for --episode-profile (default: random, always "
        "printed and recorded in session.json)",
    )
    parser.add_argument(
        "--null-video-ticks",
        type=int,
        metavar="TICKS",
        help="Verification only: run headless on the null video driver for TICKS "
        "game ticks, then exit (one game day is 74 ticks)",
    )
    args = parser.parse_args()

    if args.capture:
        args.wait = True
        args.log_commands = True
    if args.episode_profile:
        args.new_game = True

    save_path = Path(args.save)
    if not save_path.is_absolute():
        save_path = (ROOT / save_path).resolve()
    if not args.new_game and not save_path.exists():
        raise SystemExit(f"Save file does not exist: {save_path}")

    seed = None
    session = None
    if args.episode_profile:
        seed = args.seed if args.seed is not None else random.randrange(1, 2**31)
        run_root = EPISODE_ROOT
        cfg_path, session = prepare_episode_root(run_root, args.horizon_years, seed)
    else:
        run_root, cfg_path = prepare_visual_root()
    env = build_env(run_root)
    video = (
        f"null:ticks={args.null_video_ticks}"
        if args.null_video_ticks is not None
        else "sdl"
    )
    cmd = build_command(
        save_path,
        cfg_path,
        log_commands=args.log_commands,
        new_game=args.new_game,
        seed=seed,
        video=video,
        script_debug=args.episode_profile,
    )
    if args.print_command:
        print(" ".join(cmd))
        return 0

    # Snapshot pre-existing saves before launch so capture copies only what this
    # session creates (the runtime root is reused and accumulates saves).
    before_saves = save_snapshot(run_root) if args.capture else {}

    # Redirect OpenTTD's stdout/stderr to a log file so the launching terminal
    # stays quiet. The child dups the fd at fork, so closing our handle here is
    # safe and the process keeps writing to the log after this function returns.
    log_path = run_root / "openttd.log"
    with open(log_path, "w") as log_fh:
        proc = subprocess.Popen(
            cmd, cwd=str(run_root), env=env, stdout=log_fh, stderr=subprocess.STDOUT
        )
    print(f"OPEN_TTD_PID={proc.pid}")
    if args.episode_profile and session is not None:
        print(
            f"SAVE=(new {2**EPISODE_MAP_LOG2}x{2**EPISODE_MAP_LOG2} game, "
            f"{EPISODE_TOWNS} towns, start {EPISODE_STARTING_YEAR})"
        )
        print(f"SEED={seed}")
        print(f"HORIZON={session['horizon_target']} (game pauses permanently)")
    else:
        print(f"SAVE={'(new 64x64 game)' if args.new_game else save_path}")
    print(f"RUNTIME_ROOT={run_root}")
    print(f"LOG={log_path}")
    if args.log_commands:
        print(f"COMMAND_LOG={command_log_path(run_root)}")
    print(
        "VIDEO=sdl software path (-v sdl, no OpenGL); OpenTTD output redirected to LOG to keep this terminal quiet"
    )
    if args.wait:
        rc = proc.wait()
        if args.capture:
            capture_session(run_root, args.capture, before_saves)
        return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
