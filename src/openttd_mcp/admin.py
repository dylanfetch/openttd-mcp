"""Admin-port lifecycle client for the OpenTTD dedicated server.

This is the lifecycle/referee lane: connect + RCON with deterministic END
framing + update registration + typed lifecycle helpers (save/load/poll/quit).
It never issues gameplay or company commands — the Squirrel NoAI file/log
bridge stays the only hands channel. Keep it that way: adding a
construction helper here would create a second, unevidenced action path.

Hand-rolled on purpose. pyOpenTTDAdmin 1.1.0 was rejected: no ADMIN_POLL
support, a CPython-3.12-only compiled module imported even for insecure login,
and an undeclared dependency. The wire format is trivial (uint16-LE size incl.
header, uint8 type, payload) and every protocol fact below was observed
against the packaged 15.1.

Every wait has a deadline and, on timeout, names the signal that never
arrived — the same contract as the stdout readers in lifecycle.py.
"""

from __future__ import annotations

import secrets
import socket
import struct
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --- admin packet types (tcp_admin.h, protocol v3) ---
ADMIN_JOIN = 0
ADMIN_QUIT = 1
ADMIN_UPDATE_FREQUENCY = 2
ADMIN_POLL = 3
ADMIN_RCON = 5

SERVER_ERROR = 102
SERVER_PROTOCOL = 103
SERVER_WELCOME = 104
SERVER_NEWGAME = 105
SERVER_SHUTDOWN = 106
SERVER_DATE = 107
SERVER_COMPANY_NEW = 113
SERVER_COMPANY_INFO = 114
SERVER_COMPANY_ECONOMY = 117
SERVER_COMPANY_STATS = 118
SERVER_RCON = 120
SERVER_CONSOLE = 121
SERVER_RCON_END = 125

PACKET_NAMES = {
    100: "SERVER_FULL",
    101: "SERVER_BANNED",
    102: "SERVER_ERROR",
    103: "SERVER_PROTOCOL",
    104: "SERVER_WELCOME",
    105: "SERVER_NEWGAME",
    106: "SERVER_SHUTDOWN",
    107: "SERVER_DATE",
    108: "SERVER_CLIENT_JOIN",
    109: "SERVER_CLIENT_INFO",
    110: "SERVER_CLIENT_UPDATE",
    111: "SERVER_CLIENT_QUIT",
    112: "SERVER_CLIENT_ERROR",
    113: "SERVER_COMPANY_NEW",
    114: "SERVER_COMPANY_INFO",
    115: "SERVER_COMPANY_UPDATE",
    116: "SERVER_COMPANY_REMOVE",
    117: "SERVER_COMPANY_ECONOMY",
    118: "SERVER_COMPANY_STATS",
    119: "SERVER_CHAT",
    120: "SERVER_RCON",
    121: "SERVER_CONSOLE",
    122: "SERVER_CMD_NAMES",
    123: "SERVER_CMD_LOGGING_OLD",
    124: "SERVER_GAMESCRIPT",
    125: "SERVER_RCON_END",
    126: "SERVER_PONG",
    127: "SERVER_CMD_LOGGING",
}

# --- update types + frequencies (tcp_admin.h) ---
UPDATE_DATE = 0
UPDATE_CLIENT_INFO = 1
UPDATE_COMPANY_INFO = 2
UPDATE_COMPANY_ECONOMY = 3
UPDATE_COMPANY_STATS = 4
UPDATE_CHAT = 5
UPDATE_CONSOLE = 6
UPDATE_CMD_NAMES = 7
UPDATE_CMD_LOGGING = 8
UPDATE_GAMESCRIPT = 9

FREQ_POLL = 0x01
FREQ_DAILY = 0x02
FREQ_WEEKLY = 0x04
FREQ_MONTHLY = 0x08
FREQ_QUARTERLY = 0x10
FREQ_ANUALLY = 0x20
FREQ_AUTOMATIC = 0x40

ALL_IDS = 0xFFFFFFFF

# Registration matrix as sent by 15.1 in SERVER_PROTOCOL (observed).
# A type/frequency pair outside this matrix makes the server drop the
# connection without any SERVER_ERROR (observed), so it is enforced
# client-side before anything is sent.
SUPPORTED_REGISTRATIONS: dict[int, int] = {
    UPDATE_DATE: FREQ_POLL
    | FREQ_DAILY
    | FREQ_WEEKLY
    | FREQ_MONTHLY
    | FREQ_QUARTERLY
    | FREQ_ANUALLY,
    UPDATE_CLIENT_INFO: FREQ_POLL | FREQ_AUTOMATIC,
    UPDATE_COMPANY_INFO: FREQ_POLL | FREQ_AUTOMATIC,
    UPDATE_COMPANY_ECONOMY: FREQ_POLL
    | FREQ_WEEKLY
    | FREQ_MONTHLY
    | FREQ_QUARTERLY
    | FREQ_ANUALLY,
    UPDATE_COMPANY_STATS: FREQ_POLL
    | FREQ_WEEKLY
    | FREQ_MONTHLY
    | FREQ_QUARTERLY
    | FREQ_ANUALLY,
    UPDATE_CHAT: FREQ_AUTOMATIC,
    UPDATE_CONSOLE: FREQ_AUTOMATIC,
    UPDATE_CMD_NAMES: FREQ_POLL,
    UPDATE_CMD_LOGGING: FREQ_AUTOMATIC,
    UPDATE_GAMESCRIPT: FREQ_AUTOMATIC,
}

RCON_MAX_COMMAND_BYTES = 500
SAVE_COMPLETION_MARKER = "successfully saved"

DEFAULT_ADMIN_PORT = 3977


def allocate_admin_port(host: str = "127.0.0.1") -> int:
    """Reserve a free ephemeral port for this run's admin listener.

    The fixed default port collides when a stale server from a crashed run
    still holds it (or two runs overlap): the new client TCP-connects to the
    foreign server, its per-run password is rejected, and the run dies with an
    opaque socket-closed error. The bind-probe has a close-to-rebind window,
    but the OS won't hand the same ephemeral port to another probe meanwhile.
    """
    with socket.socket() as probe:
        probe.bind((host, 0))
        return probe.getsockname()[1]


def wait_for_save_on_disk(save_path: Path, disk_timeout: float) -> int | None:
    """Bounded poll for a save file to land on disk; returns its size or None.

    OpenTTD flushes saves asynchronously and WSL2 can surface the file a beat
    after the completion line, so both lifecycle lanes confirm the bytes with
    the same poll and raise their own lane-specific error on None.
    """
    deadline = time.monotonic() + disk_timeout
    while time.monotonic() < deadline:
        try:
            size = save_path.stat().st_size
        except FileNotFoundError:
            size = 0
        if size > 0:
            return size
        time.sleep(0.1)
    return None


@dataclass
class AdminLifecycleConfig:
    """Admin lane settings for one run. The password is a per-run local
    secret; it goes into the generated openttd.cfg but must never be copied
    into run evidence metadata."""

    port: int = DEFAULT_ADMIN_PORT
    password: str = field(default_factory=lambda: secrets.token_hex(16))
    client_name: str = "openttd-mcp-lifecycle"

    def public_metadata(self) -> dict[str, Any]:
        """What run evidence may record (reproducibility without the secret)."""
        return {
            "admin_port": self.port,
            "admin_client_name": self.client_name,
            "allow_insecure_admin_login": True,
            "admin_password": "<per-run generated; not recorded>",
        }


class AdminWaitTimeout(TimeoutError):
    """A bounded admin-lane wait expired; the message names the missing signal."""


class _Reader:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.pos = 0

    def u8(self) -> int:
        v = self.payload[self.pos]
        self.pos += 1
        return v

    def u16(self) -> int:
        v = struct.unpack_from("<H", self.payload, self.pos)[0]
        self.pos += 2
        return v

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.payload, self.pos)[0]
        self.pos += 4
        return v

    def i64(self) -> int:
        v = struct.unpack_from("<q", self.payload, self.pos)[0]
        self.pos += 8
        return v

    def u64(self) -> int:
        v = struct.unpack_from("<Q", self.payload, self.pos)[0]
        self.pos += 8
        return v

    def string(self) -> str:
        end = self.payload.index(0, self.pos)
        v = self.payload[self.pos : end].decode("utf-8", "replace")
        self.pos = end + 1
        return v

    def remaining(self) -> int:
        return len(self.payload) - self.pos


def encode_packet(packet_type: int, payload: bytes = b"") -> bytes:
    return struct.pack("<HB", 2 + 1 + len(payload), packet_type) + payload


def zstr(value: str) -> bytes:
    return value.encode("utf-8") + b"\0"


def parse_protocol(payload: bytes) -> dict[str, Any]:
    r = _Reader(payload)
    version = r.u8()
    updates: dict[int, int] = {}
    while r.remaining() > 0:
        if not r.u8():
            break
        update_type = r.u16()
        frequencies = r.u16()
        updates[update_type] = frequencies
    return {"version": version, "updates": updates}


def parse_welcome(payload: bytes) -> dict[str, Any]:
    r = _Reader(payload)
    return {
        "server_name": r.string(),
        "openttd_version": r.string(),
        "dedicated": bool(r.u8()),
        "map_name": r.string(),
        "seed": r.u32(),
        "landscape": r.u8(),
        "start_date": r.u32(),
        "map_width": r.u16(),
        "map_height": r.u16(),
    }


def parse_company_economy(payload: bytes) -> dict[str, Any]:
    # Field layout parses observed 15.1 payloads with 0 bytes left.
    r = _Reader(payload)
    out: dict[str, Any] = {
        "company_id": r.u8(),
        "money": r.i64(),
        "current_loan": r.u64(),
        "income": r.i64(),
        "delivered_cargo_this_quarter": r.u16(),
    }
    out["quarters"] = [
        {
            "company_value": r.i64(),
            "performance_history": r.u16(),
            "delivered_cargo": r.u16(),
        }
        for _ in range(2)
    ]
    return out


def parse_company_stats(payload: bytes) -> dict[str, Any]:
    """Decode the stable company vehicle/station counts from protocol v3."""
    r = _Reader(payload)
    out: dict[str, Any] = {
        "company_id": r.u8(),
        "trains": r.u16(),
        "lorries": r.u16(),
        "buses": r.u16(),
        "planes": r.u16(),
        "ships": r.u16(),
        "train_stations": r.u16(),
        "lorry_stations": r.u16(),
        "bus_stops": r.u16(),
        "airports": r.u16(),
        "harbours": r.u16(),
    }
    out["vehicle_count"] = sum(
        int(out[name]) for name in ("trains", "lorries", "buses", "planes", "ships")
    )
    return out


def _decode(typ: int, payload: bytes) -> dict[str, Any]:
    if typ == SERVER_PROTOCOL:
        return parse_protocol(payload)
    if typ == SERVER_WELCOME:
        return parse_welcome(payload)
    if typ == SERVER_DATE:
        return {"date_days": _Reader(payload).u32()}
    if typ == SERVER_COMPANY_ECONOMY:
        return parse_company_economy(payload)
    if typ == SERVER_COMPANY_STATS:
        return parse_company_stats(payload)
    if typ == SERVER_RCON:
        r = _Reader(payload)
        return {"colour": r.u16(), "text": r.string()}
    if typ == SERVER_RCON_END:
        return {"command": _Reader(payload).string()}
    if typ == SERVER_CONSOLE:
        r = _Reader(payload)
        return {"origin": r.string(), "text": r.string()}
    if typ == SERVER_ERROR:
        return {"error_code": _Reader(payload).u8() if payload else None}
    return {"payload_len": len(payload)}


class AdminClient:
    """One admin-port connection to a dedicated server.

    Owns the socket and a pull-based receive loop that separates RCON-framed
    replies from registered pushes and lifecycle packets: packets a wait is
    not looking for are parked in ``pending`` (never dropped), and the last
    packets seen feed every timeout diagnostic.
    """

    def __init__(self, config: AdminLifecycleConfig, host: str = "127.0.0.1") -> None:
        self.config = config
        self.host = host
        self.sock: socket.socket | None = None
        self.protocol: dict[str, Any] | None = None
        self.welcome: dict[str, Any] | None = None
        self.pending: deque[tuple[int, dict[str, Any]]] = deque()
        self._recent: deque[str] = deque(maxlen=16)

    # --- connection ---

    def connect(
        self, timeout: float = 30.0, *, register_console: bool = True
    ) -> dict[str, Any]:
        """Insecure loopback login; waits for SERVER_PROTOCOL + SERVER_WELCOME.

        The listener only opens once admin auth is configured, so retry the
        TCP connect until the deadline — "listener never opened" is its own
        diagnostic, distinct from "welcome never arrived".
        """
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while self.sock is None:
            if time.monotonic() >= deadline:
                raise AdminWaitTimeout(
                    f"admin listener on {self.host}:{self.config.port} never accepted a "
                    f"connection within {timeout:.0f}s (last error: {last_error}); is "
                    "admin_password/allow_insecure_admin_login in the generated openttd.cfg?"
                )
            try:
                self.sock = socket.create_connection(
                    (self.host, self.config.port),
                    timeout=min(1.0, max(0.1, deadline - time.monotonic())),
                )
            except OSError as exc:
                last_error = exc
                time.sleep(0.2)
        self.sock.sendall(
            encode_packet(
                ADMIN_JOIN,
                zstr(self.config.password) + zstr(self.config.client_name) + zstr("1"),
            )
        )
        _, protocol = self._wait_for(
            {SERVER_PROTOCOL}, deadline, "SERVER_PROTOCOL (auth reply)"
        )
        _, welcome = self._wait_for(
            {SERVER_WELCOME}, deadline, "SERVER_WELCOME (auth reply)"
        )
        self.protocol = protocol
        self.welcome = welcome
        if register_console:
            # Console pushes carry async save-completion lines; register the
            # one AUTOMATIC feed every lifecycle helper relies on.
            self.register(UPDATE_CONSOLE, FREQ_AUTOMATIC)
        return welcome

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.sock.close()
            self.sock = None

    # --- wire primitives ---

    def _recv_exact(self, n: int, deadline: float, description: str) -> bytes:
        assert self.sock is not None
        chunks = b""
        while len(chunks) < n:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AdminWaitTimeout(
                    f"timed out waiting for {description}; " + self._recent_summary()
                )
            self.sock.settimeout(min(remaining, 1.0))
            try:
                chunk = self.sock.recv(n - len(chunks))
            except TimeoutError:
                continue
            if not chunk:
                raise ConnectionError(
                    f"admin socket closed by server while waiting for {description}; "
                    + self._recent_summary()
                )
            chunks += chunk
        return chunks

    def _next_packet(
        self, deadline: float, description: str
    ) -> tuple[int, dict[str, Any]]:
        header = self._recv_exact(2, deadline, description)
        size = struct.unpack("<H", header)[0]
        body = self._recv_exact(size - 2, deadline, description)
        typ, payload = body[0], body[1:]
        decoded = _decode(typ, payload)
        self._recent.append(PACKET_NAMES.get(typ, str(typ)))
        return typ, decoded

    def _recent_summary(self) -> str:
        recent = ", ".join(self._recent) if self._recent else "none"
        parked = (
            ", ".join(PACKET_NAMES.get(t, str(t)) for t, _ in self.pending) or "none"
        )
        return f"packets seen so far: [{recent}]; parked pushes: [{parked}]"

    _PENDING_LIMIT = 512

    def _park(self, typ: int, decoded: dict[str, Any]) -> None:
        """Park a packet no active wait wanted. Console pushes are diagnostic
        noise (every script log line becomes one) and may be pruned oldest-
        first; lifecycle/data packets are never dropped."""
        self.pending.append((typ, decoded))
        if len(self.pending) > self._PENDING_LIMIT:
            for idx, (parked_typ, _) in enumerate(self.pending):
                if parked_typ == SERVER_CONSOLE:
                    del self.pending[idx]
                    break

    def _wait_for(
        self,
        wanted: set[int],
        deadline: float,
        description: str,
        *,
        predicate: Callable[[int, dict[str, Any]], bool] | None = None,
        discard_unmatched_wanted: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        """Return the first matching packet; park everything else in pending."""
        matches = predicate or (lambda _typ, _decoded: True)
        idx = 0
        while idx < len(self.pending):
            typ, decoded = self.pending[idx]
            if typ in wanted and matches(typ, decoded):
                del self.pending[idx]
                return typ, decoded
            if discard_unmatched_wanted and typ in wanted:
                del self.pending[idx]
                continue
            idx += 1
        while True:
            if time.monotonic() >= deadline:
                raise AdminWaitTimeout(
                    f"timed out waiting for {description}; " + self._recent_summary()
                )
            typ, decoded = self._next_packet(deadline, description)
            if typ in wanted and matches(typ, decoded):
                return typ, decoded
            if discard_unmatched_wanted and typ in wanted:
                continue
            self._park(typ, decoded)

    # --- protocol surface ---

    def rcon(self, command: str, timeout: float = 30.0) -> list[str]:
        """Run a console command; collect SERVER_RCON lines until SERVER_RCON_END."""
        assert self.sock is not None, "connect() first"
        encoded = command.encode("utf-8")
        if len(encoded) > RCON_MAX_COMMAND_BYTES:
            raise ValueError(
                f"rcon command is {len(encoded)} bytes; the admin protocol caps "
                f"commands at {RCON_MAX_COMMAND_BYTES} bytes: {command[:80]!r}…"
            )
        self.sock.sendall(encode_packet(ADMIN_RCON, zstr(command)))
        deadline = time.monotonic() + timeout
        lines: list[str] = []
        while True:
            typ, decoded = self._wait_for(
                {SERVER_RCON, SERVER_RCON_END},
                deadline,
                f"SERVER_RCON_END for rcon {command.split(' ', 1)[0]!r}",
            )
            if typ == SERVER_RCON:
                lines.append(decoded["text"])
            else:
                return lines

    def register(self, update_type: int, frequency: int) -> None:
        """Register a push; only observed-supported pairs are allowed client-side."""
        assert self.sock is not None, "connect() first"
        allowed = SUPPORTED_REGISTRATIONS.get(update_type)
        if allowed is None:
            raise ValueError(f"unknown admin update type {update_type}")
        if frequency & ~allowed:
            raise ValueError(
                f"registration ({update_type}, {frequency:#x}) is outside the supported "
                f"matrix (allowed mask for this type: {allowed:#x}); the server drops the "
                "connection on illegal registrations, so this is refused locally"
            )
        self.sock.sendall(
            encode_packet(
                ADMIN_UPDATE_FREQUENCY, struct.pack("<HH", update_type, frequency)
            )
        )

    def _poll(self, update_type: int, ident: int = 0) -> None:
        assert self.sock is not None, "connect() first"
        self.sock.sendall(
            encode_packet(ADMIN_POLL, struct.pack("<BI", update_type, ident))
        )

    # --- typed lifecycle helpers ---

    def poll_date(self, timeout: float = 10.0) -> int:
        """Current in-game date in days-since-year-0."""
        self._poll(UPDATE_DATE)
        deadline = time.monotonic() + timeout
        _, decoded = self._wait_for({SERVER_DATE}, deadline, "SERVER_DATE poll reply")
        return decoded["date_days"]

    def wait_for_date(self, at_least: int, timeout: float = 30.0) -> int:
        """Wait for a registered date update at or beyond ``at_least``.

        Callers must first register ``UPDATE_DATE`` with a pushed frequency.
        Older queued date packets are skipped by the predicate rather than
        mistaken for the requested boundary.
        """
        if isinstance(at_least, bool) or not isinstance(at_least, int):
            raise ValueError(f"at_least must be an integer date, got {at_least!r}")
        deadline = time.monotonic() + timeout
        _, decoded = self._wait_for(
            {SERVER_DATE},
            deadline,
            f"SERVER_DATE at or beyond {at_least}",
            predicate=lambda _typ, row: row.get("date_days", -1) >= at_least,
            discard_unmatched_wanted=True,
        )
        return decoded["date_days"]

    def poll_economy(
        self, timeout: float = 10.0, settle: float = 0.5
    ) -> list[dict[str, Any]]:
        """Economy of every company (one packet per company).

        The protocol gives no count, so after the first packet keep collecting
        until the stream stays quiet for ``settle`` seconds.
        """
        self._poll(UPDATE_COMPANY_ECONOMY, ALL_IDS)
        deadline = time.monotonic() + timeout
        _, first = self._wait_for(
            {SERVER_COMPANY_ECONOMY}, deadline, "SERVER_COMPANY_ECONOMY poll reply"
        )
        companies = [first]
        while True:
            try:
                _, more = self._wait_for(
                    {SERVER_COMPANY_ECONOMY},
                    min(deadline, time.monotonic() + settle),
                    "additional SERVER_COMPANY_ECONOMY packets",
                )
                companies.append(more)
            except AdminWaitTimeout:
                return companies

    def poll_company_stats(
        self, timeout: float = 10.0, settle: float = 0.5
    ) -> list[dict[str, Any]]:
        """Current vehicle and station counts for every company."""
        self._poll(UPDATE_COMPANY_STATS, ALL_IDS)
        deadline = time.monotonic() + timeout
        _, first = self._wait_for(
            {SERVER_COMPANY_STATS}, deadline, "SERVER_COMPANY_STATS poll reply"
        )
        companies = [first]
        while True:
            try:
                _, more = self._wait_for(
                    {SERVER_COMPANY_STATS},
                    min(deadline, time.monotonic() + settle),
                    "additional SERVER_COMPANY_STATS packets",
                )
                companies.append(more)
            except AdminWaitTimeout:
                return companies

    def save_and_confirm(
        self, save_path: Path, timeout: float = 60.0, disk_timeout: float = 10.0
    ) -> dict[str, Any]:
        """rcon save, wait for the completion line, confirm the file on disk.

        The completion line lands inside the RCON framing when the save is
        fast (observed on 64x64) and as a console push when the save
        thread outlives the command (research-verified for larger maps);
        accept either. A missed completion line is still tolerated when the
        file lands on disk — the same escape hatch as the stdin lane. The file
        check has its own bounded poll because WSL2 can surface the file a
        beat after the completion line.
        """
        stem = str(save_path)
        stem = stem.removesuffix(".sav")
        deadline = time.monotonic() + timeout
        lines = self.rcon(f"save {stem}", timeout=timeout)
        channel = "rcon_framing"
        marker_error: AdminWaitTimeout | None = None
        if not any(SAVE_COMPLETION_MARKER in line for line in lines):
            channel = "console_push"
            try:
                self._wait_for(
                    {SERVER_CONSOLE},
                    deadline,
                    f"console push containing {SAVE_COMPLETION_MARKER!r} after rcon save "
                    "(is CONSOLE/AUTOMATIC registered?)",
                    predicate=lambda _typ, decoded: (
                        SAVE_COMPLETION_MARKER in decoded.get("text", "")
                    ),
                )
            except AdminWaitTimeout as exc:
                channel = "marker_missed_disk_only"
                marker_error = exc
        size = wait_for_save_on_disk(save_path, disk_timeout)
        if size is not None:
            return {"channel": channel, "size": size}
        if marker_error is not None:
            raise AdminWaitTimeout(
                f"neither the save completion line nor {save_path} arrived "
                f"(disk checked for {disk_timeout:.0f}s after: {marker_error})"
            )
        raise AdminWaitTimeout(
            f"save completion line arrived (via {channel}) but {save_path} never "
            f"appeared (or stayed empty) within {disk_timeout:.0f}s"
        )

    def _wait_ready_after_reload(self, deadline: float, what: str) -> dict[str, Any]:
        self._wait_for({SERVER_NEWGAME}, deadline, f"SERVER_NEWGAME after {what}")
        _, welcome = self._wait_for(
            {SERVER_WELCOME},
            deadline,
            f"re-sent SERVER_WELCOME after {what} (map ready signal)",
        )
        self.welcome = welcome
        return welcome

    def load_and_wait_ready(
        self, save_path: Path, timeout: float = 120.0
    ) -> dict[str, Any]:
        """rcon load by name, then wait SERVER_NEWGAME -> re-sent SERVER_WELCOME.

        The console's load command resolves its argument against the scanned
        savegame list — the game's own save directory, refreshed by the game's
        own saves — so an absolute path never matches and an externally copied
        file may not be seen. The working recipe (probed live): save_and_confirm()
        to a path inside the game's save
        directory (<run_root>/save/<name>.sav), then load here by bare name.
        A reply naming the file as not found raises immediately rather than
        letting the SERVER_NEWGAME wait run out the full timeout.

        Registrations and the socket survive the reload (dedicated server;
        verified), so the caller keeps using this client afterwards.
        """
        deadline = time.monotonic() + timeout
        lines = self.rcon(f"load {save_path.name}", timeout=timeout)
        rejections = ("cannot be found", "is not a savegame")
        not_found = [
            line for line in lines if any(marker in line for marker in rejections)
        ]
        if not_found:
            raise AdminWaitTimeout(
                f"rcon load {save_path.name} was rejected: {not_found[0]!r} "
                "(the console loads only from its scanned save directory; "
                "save via save_and_confirm() into <run_root>/save/ first)"
            )
        return self._wait_ready_after_reload(deadline, f"rcon load {save_path.name}")

    def new_game(
        self, seed: int | None = None, timeout: float = 120.0
    ) -> dict[str, Any]:
        """rcon newgame [seed], then wait SERVER_NEWGAME -> re-sent SERVER_WELCOME."""
        deadline = time.monotonic() + timeout
        command = "newgame" if seed is None else f"newgame {seed}"
        self.rcon(command, timeout=timeout)
        return self._wait_ready_after_reload(deadline, f"rcon {command}")

    def shutdown(
        self, proc: Any | None = None, timeout: float = 20.0
    ) -> dict[str, Any]:
        """rcon quit, drain until the server closes the socket, wait for exit.

        Escalates to kill() only after the timeout, and says so in the result
        so evidence never mistakes a forced kill for a graceful quit.
        """
        assert self.sock is not None, "connect() first"
        result: dict[str, Any] = {"graceful": False, "escalation": None}
        deadline = time.monotonic() + timeout
        self.sock.sendall(encode_packet(ADMIN_RCON, zstr("quit")))
        packets_after_quit: list[str] = []
        try:
            while True:
                typ, _ = self._next_packet(
                    deadline, "server socket close after rcon quit"
                )
                packets_after_quit.append(PACKET_NAMES.get(typ, str(typ)))
        except ConnectionError:
            result["graceful"] = True
        except AdminWaitTimeout:
            result["escalation"] = (
                f"server socket still open {timeout:.0f}s after rcon quit"
            )
        finally:
            result["packets_after_quit"] = packets_after_quit
            self.close()
        if proc is not None:
            try:
                result["exit_code"] = proc.wait(
                    timeout=max(1.0, deadline - time.monotonic())
                )
            except Exception:  # noqa: BLE001 -- subprocess.TimeoutExpired; keep dependency-free
                proc.kill()
                result["graceful"] = False
                result["escalation"] = (
                    f"process did not exit within {timeout:.0f}s of rcon quit; killed"
                )
                result["exit_code"] = proc.wait(timeout=5)
        return result
