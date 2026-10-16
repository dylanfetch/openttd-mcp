# Local OpenTTD Runtime Setup

The project runs a locally extracted OpenTTD 15.1 — no system install, no sudo.
`src/openttd_mcp/runtime.py` expects the tree under:

```text
<repo>/.local/openttd-root/
```

with the binary at `.local/openttd-root/usr/games/openttd` and its shared
libraries at `.local/openttd-root/usr/lib/x86_64-linux-gnu`.

## Supported package source

The checked recipe is for **Ubuntu 26.04 LTS, code-named
Resolute, on the 64-bit x86 (`amd64`) architecture**. Packages came from
`http://archive.ubuntu.com/ubuntu`, suite `resolute`, components `main` and
`universe`. The runtime module currently hardcodes the
`usr/lib/x86_64-linux-gnu` layout, so this is not an architecture-neutral
recipe.

The extracted tree is also not a hermetic Linux distribution: OpenTTD still
uses ordinary host libraries outside the nine packages pinned in the setup
script. Other distributions and architectures need a separately verified
package set and a corresponding runtime-layout change.

## Recreating the runtime

The host's APT configuration must already enable
Resolute `main` and `universe` and have current package lists. Enabling sources
or refreshing those lists is a host-administration step that may require sudo;
the project recipe below still performs no system install and needs no sudo.
Exact versions can leave the live archive when Ubuntu supersedes them; the
script does not substitute a different version. Retrieve the exact package
files from `snapshot.ubuntu.com`, give them the manifest filenames, place them
in `.local/debs/`, and rerun the same command. The script accepts those files
only when their SHA-256 digests match the pinned
manifest.

From the repository root, run:

```console
python3 tools/setup_openttd_runtime.py
```

Do not rerun setup while an episode is using runtime data through symbolic
links (symlinks); replacement changes the tree those links resolve into.

The script keeps downloaded Debian package (`.deb`) files in `.local/debs/` and
temporary extraction under `.local/`. It rejects a corrupt expected cache file,
reuses each valid cached package, downloads missing pins into temporary staging,
and moves only verified files into the persistent cache. It verifies all nine
cached digests again before extracting any package. Each run builds a fresh
candidate, requires the binary, base-set, language, and library paths, and runs
the candidate binary with `-h` before replacing `.local/openttd-root`. A failed
run leaves an existing runtime unchanged.

The final replacement uses `.local/openttd-root.previous` as a sibling backup.
An ordinary interruption restores it immediately. After a hard process kill,
the next setup run restores it when the canonical runtime is absent; when both
paths exist, the canonical runtime wins and the stale backup is removed.

The graphics/music base sets (`opengfx`, `openmsx`) and the shared libraries
(`libicu`, `libfluidsynth`, `libinstpatch`, `libsdl3`) are required — the bare
`openttd` package fails to launch without them.

## Repository-root override

A source checkout discovers its own repository root. An installed package or a
nonstandard layout can set `OPENTTD_MCP_REPO_ROOT` to the working root that
**contains** `.local/openttd-root`:

```bash
export OPENTTD_MCP_REPO_ROOT=/absolute/path/to/working-root
```

Do not point it at `.local/openttd-root` itself. Set it before starting Python;
the runtime paths are bound when `openttd_mcp.runtime` is imported.

## Quiet WSLg visual launch

The launcher's quiet WSLg (Windows Subsystem for Linux GUI) path uses the plain
`sdl` video driver — not `sdl-opengl` — with the software-GL fallbacks
`LIBGL_ALWAYS_SOFTWARE` and `MESA_LOADER_DRIVER_OVERRIDE`, and redirects
OpenTTD's output to `openttd.log`. `SDL_RENDER_DRIVER` is a no-op for OpenTTD
(its SDL driver does not use `SDL_Renderer`) and must not be advertised as the
mechanism.

## Invoking the binary directly

The runners set this up themselves (via `openttd_mcp.runtime`); for manual use:

```bash
cd /ABSOLUTE/PATH/TO/openttd-mcp
LD_LIBRARY_PATH="$PWD/.local/openttd-root/usr/lib/x86_64-linux-gnu" \
  "$PWD/.local/openttd-root/usr/games/openttd" -h
```

Runtime roots, freedesktop.org base-directory homes (`XDG_*`), downloads, and
setup staging stay under ignored `.local/` paths per the artifact policy in
`AGENTS.md`.
