# Sessions and saved games

## New games

`openttd-mcp play` starts one headless game and serves it over MCP stdio for the
lifetime of the client connection. The game runs in real time while the
connection remains open, including while the agent is thinking. A reconnect
starts a new game.

You can append options to the client command's argument list:

```console
openttd-mcp play --seed 42 --starting-year 1990 --map-log2 7 --num-towns 16
```

`--map-log2 7` means a 128 × 128 map. `--run-dir /absolute/path/to/new-directory`
selects the output directory; use a fresh directory for each session.
`openttd-mcp play --help` lists all options. The default `play` tools use text
observations; installing the optional `vision` dependency alone does not add
vision tools to this command.

## Outputs and stopping

The default output is `.local/runs/<timestamp>-play/` below the root selected by
`OPENTTD_MCP_REPO_ROOT`. Source checkouts infer their root, but installed wheels
need the variable set explicitly. Runtime paths are read at process startup.

| File | Contents |
| --- | --- |
| `play_startup.json` | Successful startup and game settings |
| `registry.json` | Tools exposed to the client |
| `tool_calls.jsonl` | Calls and results, one JSON record per line |
| `openttd.log` | OpenTTD process output |
| `play_cleanup.json` | Shutdown status, errors, and final save path |
| `final.sav` | Game saved during successful shutdown |

Disconnect or close the client to stop its game. Let the server finish saving
and quitting. `end_episode` disables further gameplay tool calls; in a plain
`play` session it does not close the client connection or shut down the process.
Disconnect afterward to trigger saving and cleanup.
Normal shutdown attempts a save; power loss or force-killing processes cannot
guarantee one. Keep runs you care about before deleting `.local/`.

## Look at a save

With a graphical desktop or WSLg, open a save using the visual launcher:

```console
uv run --locked --no-dev openttd-playtest /absolute/path/to/final.sav
```

For a human-controlled fresh game, `openttd-playtest --new-game` opens a 64 × 64
map. These graphical paths need a display and are separate from the headless
MCP setup check.

## Agent inspection of completed saves

The save inspector exposes observations without gameplay construction tools.
Give it an explicit save path by changing the client command's final arguments
from `openttd-mcp play` to:

```console
openttd-mcp-inspector /absolute/path/to/final.sav
```

Use a separate server name such as `openttd-review`, the same root environment,
and the same client timeouts. The inspector loads the save in a separate game
process; its review outputs do not overwrite the input save. For details of the
available tools without launching a game:

```console
uv run --locked --no-dev openttd-mcp-inspector /absolute/path/to/final.sav --dump-registry
```

Independent bus/freight save verifiers are also available as Python modules,
for example `python -m openttd_mcp.verify.bus_network --help`. They test network
facts through a fresh game process. A normal `play` session does not run them
automatically or produce a benchmark score.
