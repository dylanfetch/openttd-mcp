"""AdminClient against a fake admin server speaking the wire format.

No real OpenTTD here (consistent with the suite); the protocol facts the fake
server encodes — packet layout, RCON framing, WELCOME-after-NEWGAME
sequencing, the registration matrix, silent disconnect on illegal
registration — were pinned live by the 2026-07-02 spike in
`.local/admin-spike/` against the packaged 15.1 dedicated server.
"""

from __future__ import annotations

import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path

from openttd_mcp.admin import (
    ADMIN_JOIN,
    ADMIN_POLL,
    ADMIN_QUIT,
    ADMIN_RCON,
    ADMIN_UPDATE_FREQUENCY,
    ALL_IDS,
    FREQ_AUTOMATIC,
    FREQ_DAILY,
    SERVER_COMPANY_ECONOMY,
    SERVER_COMPANY_STATS,
    SERVER_CONSOLE,
    SERVER_DATE,
    SERVER_NEWGAME,
    SERVER_PROTOCOL,
    SERVER_RCON,
    SERVER_RCON_END,
    SERVER_SHUTDOWN,
    SERVER_WELCOME,
    SUPPORTED_REGISTRATIONS,
    UPDATE_COMPANY_ECONOMY,
    UPDATE_COMPANY_STATS,
    UPDATE_CONSOLE,
    UPDATE_DATE,
    AdminClient,
    AdminLifecycleConfig,
    AdminWaitTimeout,
    encode_packet,
    parse_company_economy,
    parse_company_stats,
    zstr,
)

PASSWORD = "test-secret"


def protocol_payload() -> bytes:
    payload = bytes([3])  # protocol version
    for update_type, mask in SUPPORTED_REGISTRATIONS.items():
        payload += bytes([1]) + struct.pack("<HH", update_type, mask)
    payload += bytes([0])
    return payload


def welcome_payload(map_name: str = "Random Map", seed: int = 1337) -> bytes:
    return (
        zstr("fake-server")
        + zstr("15.1")
        + bytes([1])
        + zstr(map_name)
        + struct.pack("<I", seed)
        + bytes([0])
        + struct.pack("<I", 712223)
        + struct.pack("<HH", 64, 64)
    )


def rcon_line(text: str, colour: int = 1) -> bytes:
    return encode_packet(SERVER_RCON, struct.pack("<H", colour) + zstr(text))


def rcon_end(command: str) -> bytes:
    return encode_packet(SERVER_RCON_END, zstr(command))


def console_push(text: str, origin: str = "console") -> bytes:
    return encode_packet(SERVER_CONSOLE, zstr(origin) + zstr(text))


def date_push(days: int) -> bytes:
    return encode_packet(SERVER_DATE, struct.pack("<I", days))


def economy_payload(company_id: int, money: int, loan: int) -> bytes:
    payload = struct.pack("<BqQq H", company_id, money, loan, 0, 0)
    for _ in range(2):
        payload += struct.pack("<qHH", 0, 0, 0)
    return payload


def company_stats_payload(company_id: int, counts: tuple[int, ...]) -> bytes:
    if len(counts) != 10:
        raise ValueError("company statistics require ten counts")
    return struct.pack("<B10H", company_id, *counts)


class FakeAdminServer(threading.Thread):
    """Single-connection admin server; behavior scripted per test."""

    def __init__(self, rcon_handler=None, poll_handler=None):
        super().__init__(daemon=True)
        self.rcon_handler = rcon_handler or (lambda command: [rcon_end(command)])
        self.poll_handler = poll_handler or (lambda update_type, ident: [])
        self.registrations: list[tuple[int, int]] = []
        self.rcon_commands: list[str] = []
        self.errors: list[str] = []
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.start()

    def config(self) -> AdminLifecycleConfig:
        return AdminLifecycleConfig(
            port=self.port, password=PASSWORD, client_name="test-client"
        )

    def _read_packet(self, conn) -> tuple[int, bytes] | None:
        header = b""
        while len(header) < 2:
            chunk = conn.recv(2 - len(header))
            if not chunk:
                return None
            header += chunk
        size = struct.unpack("<H", header)[0]
        body = b""
        while len(body) < size - 2:
            chunk = conn.recv(size - 2 - len(body))
            if not chunk:
                return None
            body += chunk
        return body[0], body[1:]

    def run(self) -> None:
        try:
            self.listener.settimeout(10)
            conn, _ = self.listener.accept()
            conn.settimeout(10)
            with conn:
                first = self._read_packet(conn)
                if first is None or first[0] != ADMIN_JOIN:
                    self.errors.append(f"expected ADMIN_JOIN first, got {first!r}")
                    return
                if not first[1].startswith(PASSWORD.encode() + b"\0"):
                    self.errors.append("bad password")
                    return
                conn.sendall(encode_packet(SERVER_PROTOCOL, protocol_payload()))
                conn.sendall(encode_packet(SERVER_WELCOME, welcome_payload()))
                while True:
                    packet = self._read_packet(conn)
                    if packet is None:
                        return
                    typ, payload = packet
                    if typ == ADMIN_RCON:
                        command = payload.rstrip(b"\0").decode()
                        self.rcon_commands.append(command)
                        for chunk in self.rcon_handler(command):
                            conn.sendall(chunk)
                        if command == "quit":
                            return
                    elif typ == ADMIN_UPDATE_FREQUENCY:
                        update_type, frequency = struct.unpack("<HH", payload)
                        self.registrations.append((update_type, frequency))
                    elif typ == ADMIN_POLL:
                        update_type, ident = struct.unpack("<BI", payload)
                        for chunk in self.poll_handler(update_type, ident):
                            conn.sendall(chunk)
                    elif typ == ADMIN_QUIT:
                        return
        except Exception as exc:  # noqa: BLE001
            self.errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            self.listener.close()


class AdminClientTests(unittest.TestCase):
    def connect_client(self, server: FakeAdminServer, **kwargs) -> AdminClient:
        client = AdminClient(server.config())
        self.addCleanup(self.assertEqual, server.errors, [])
        self.addCleanup(server.join, 2)
        self.addCleanup(client.close)
        client.connect(timeout=10, **kwargs)
        return client

    def test_connect_parses_welcome_and_registers_console(self) -> None:
        server = FakeAdminServer()
        client = self.connect_client(server)
        self.assertEqual(client.welcome["openttd_version"], "15.1")
        self.assertTrue(client.welcome["dedicated"])
        self.assertEqual(client.protocol["updates"], SUPPORTED_REGISTRATIONS)
        client.close()
        server.join()
        self.assertIn((UPDATE_CONSOLE, FREQ_AUTOMATIC), server.registrations)

    def test_rcon_collects_lines_until_end(self) -> None:
        def handler(command):
            return [rcon_line("line one"), rcon_line("line two"), rcon_end(command)]

        server = FakeAdminServer(rcon_handler=handler)
        client = self.connect_client(server, register_console=False)
        self.assertEqual(client.rcon("status"), ["line one", "line two"])

    def test_interleaved_push_during_rcon_is_parked_not_lost(self) -> None:
        def handler(command):
            return [
                rcon_line("a"),
                date_push(712300),
                rcon_line("b"),
                rcon_end(command),
            ]

        server = FakeAdminServer(rcon_handler=handler)
        client = self.connect_client(server, register_console=False)
        self.assertEqual(client.rcon("status"), ["a", "b"])
        self.assertIn(SERVER_DATE, [typ for typ, _ in client.pending])

    def test_rcon_command_over_500_bytes_is_rejected_locally(self) -> None:
        server = FakeAdminServer()
        client = self.connect_client(server, register_console=False)
        with self.assertRaises(ValueError) as ctx:
            client.rcon("save " + "x" * 500)
        self.assertIn("500", str(ctx.exception))

    def test_illegal_registration_is_rejected_locally(self) -> None:
        server = FakeAdminServer()
        client = self.connect_client(server, register_console=False)
        with self.assertRaises(ValueError) as ctx:
            client.register(UPDATE_CONSOLE, FREQ_DAILY)
        self.assertIn("drops the connection", str(ctx.exception))
        client.close()
        server.join()
        self.assertEqual(server.registrations, [])

    def test_legal_registration_reaches_the_server(self) -> None:
        server = FakeAdminServer()
        client = self.connect_client(server, register_console=False)
        client.register(UPDATE_DATE, FREQ_DAILY)
        client.close()
        server.join()
        self.assertEqual(server.registrations, [(UPDATE_DATE, FREQ_DAILY)])

    def test_poll_date(self) -> None:
        server = FakeAdminServer(
            poll_handler=lambda t, i: [date_push(712223)] if t == UPDATE_DATE else []
        )
        client = self.connect_client(server, register_console=False)
        self.assertEqual(client.poll_date(), 712223)

    def test_wait_for_date_ignores_older_daily_pushes(self) -> None:
        server = FakeAdminServer(
            poll_handler=lambda t, i: (
                [date_push(712223), date_push(712224), date_push(712225)]
                if t == UPDATE_DATE
                else []
            )
        )
        client = self.connect_client(server, register_console=False)

        self.assertEqual(client.poll_date(), 712223)
        self.assertEqual(client.wait_for_date(712225), 712225)
        self.assertNotIn(SERVER_DATE, [typ for typ, _payload in client.pending])

    def test_poll_economy_collects_every_company(self) -> None:
        def poll_handler(update_type, ident):
            if update_type != UPDATE_COMPANY_ECONOMY:
                return []
            return [
                encode_packet(
                    SERVER_COMPANY_ECONOMY, economy_payload(0, 100000, 100000)
                ),
                encode_packet(
                    SERVER_COMPANY_ECONOMY, economy_payload(1, 250000, 50000)
                ),
            ]

        server = FakeAdminServer(poll_handler=poll_handler)
        client = self.connect_client(server, register_console=False)
        companies = client.poll_economy(settle=0.3)
        self.assertEqual([c["company_id"] for c in companies], [0, 1])
        self.assertEqual(companies[1]["money"], 250000)
        self.assertEqual(companies[1]["current_loan"], 50000)

    def test_company_economy_payload_parses_exactly(self) -> None:
        parsed = parse_company_economy(economy_payload(2, -5000, 300000))
        self.assertEqual(parsed["company_id"], 2)
        self.assertEqual(parsed["money"], -5000)
        self.assertEqual(parsed["current_loan"], 300000)
        self.assertEqual(len(parsed["quarters"]), 2)

    def test_poll_company_stats_returns_the_protocol_vehicle_counts(self) -> None:
        """The guard needs current server state, not inferred vehicle-tool history."""

        def poll_handler(update_type, ident):
            if update_type != UPDATE_COMPANY_STATS or ident != ALL_IDS:
                return []
            return [
                encode_packet(
                    SERVER_COMPANY_STATS,
                    company_stats_payload(0, (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)),
                ),
                encode_packet(
                    SERVER_COMPANY_STATS,
                    company_stats_payload(1, (0, 0, 1, 0, 0, 2, 3, 4, 5, 6)),
                ),
            ]

        server = FakeAdminServer(poll_handler=poll_handler)
        client = self.connect_client(server, register_console=False)

        companies = client.poll_company_stats(settle=0.3)

        self.assertEqual([row["company_id"] for row in companies], [0, 1])
        self.assertEqual(companies[0]["vehicle_count"], 15)
        self.assertEqual(companies[0]["buses"], 3)
        self.assertEqual(companies[0]["harbours"], 10)
        self.assertEqual(
            parse_company_stats(company_stats_payload(1, (0,) * 10))["vehicle_count"],
            0,
        )

    def test_save_and_confirm_completion_inside_rcon_framing(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        save_path = Path(tmp.name) / "final.sav"

        def handler(command):
            if command.startswith("save "):
                save_path.write_bytes(b"savegame-bytes")
                return [
                    rcon_line("Saving map..."),
                    rcon_line(f"Map successfully saved to '{save_path}'."),
                    rcon_end(command),
                ]
            return [rcon_end(command)]

        server = FakeAdminServer(rcon_handler=handler)
        client = self.connect_client(server, register_console=False)
        result = client.save_and_confirm(save_path)
        self.assertEqual(result["channel"], "rcon_framing")
        self.assertGreater(result["size"], 0)

    def test_save_and_confirm_completion_as_console_push(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        save_path = Path(tmp.name) / "final.sav"

        def handler(command):
            if command.startswith("save "):
                save_path.write_bytes(b"savegame-bytes")
                return [
                    rcon_line("Saving map..."),
                    rcon_end(command),
                    console_push("Some unrelated console line"),
                    console_push(f"Map successfully saved to '{save_path}'."),
                ]
            return [rcon_end(command)]

        server = FakeAdminServer(rcon_handler=handler)
        client = self.connect_client(server, register_console=False)
        result = client.save_and_confirm(save_path)
        self.assertEqual(result["channel"], "console_push")

    def test_save_and_confirm_accepts_file_on_disk_when_marker_missed(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        save_path = Path(tmp.name) / "final.sav"

        def handler(command):
            if command.startswith("save "):
                save_path.write_bytes(b"savegame-bytes")
                return [rcon_end(command)]  # no completion line on either channel
            return [rcon_end(command)]

        server = FakeAdminServer(rcon_handler=handler)
        client = self.connect_client(server, register_console=False)
        result = client.save_and_confirm(save_path, timeout=0.6)
        self.assertEqual(result["channel"], "marker_missed_disk_only")
        self.assertGreater(result["size"], 0)

    def test_save_and_confirm_names_missing_file(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        save_path = Path(tmp.name) / "never-written.sav"

        def handler(command):
            return [rcon_line("Map successfully saved to 'x'."), rcon_end(command)]

        server = FakeAdminServer(rcon_handler=handler)
        client = self.connect_client(server, register_console=False)
        with self.assertRaises(AdminWaitTimeout) as ctx:
            client.save_and_confirm(save_path, disk_timeout=0.4)
        self.assertIn(str(save_path), str(ctx.exception))

    def test_load_waits_newgame_then_welcome(self) -> None:
        def handler(command):
            if command.startswith("load "):
                return [
                    rcon_end(command),
                    encode_packet(SERVER_NEWGAME),
                    encode_packet(
                        SERVER_WELCOME, welcome_payload(map_name="loaded", seed=42)
                    ),
                ]
            return [rcon_end(command)]

        server = FakeAdminServer(rcon_handler=handler)
        client = self.connect_client(server, register_console=False)
        welcome = client.load_and_wait_ready(Path("/tmp/game.sav"))
        self.assertEqual(welcome["map_name"], "loaded")
        self.assertEqual(client.welcome["seed"], 42)
        # The console resolves load arguments against its scanned save list,
        # so the client must send the bare name, never the absolute path.
        load_commands = [c for c in server.rcon_commands if c.startswith("load ")]
        self.assertEqual(load_commands, ["load game.sav"])

    def test_load_not_found_reply_fails_fast(self) -> None:
        def handler(command):
            if command.startswith("load "):
                return [rcon_line("'game.sav' cannot be found."), rcon_end(command)]
            return [rcon_end(command)]

        server = FakeAdminServer(rcon_handler=handler)
        client = self.connect_client(server, register_console=False)
        with self.assertRaises(AdminWaitTimeout) as ctx:
            client.load_and_wait_ready(Path("/tmp/game.sav"), timeout=5.0)
        self.assertIn("cannot be found", str(ctx.exception))
        self.assertIn("save_and_confirm", str(ctx.exception))

    def test_missing_welcome_after_newgame_names_the_signal(self) -> None:
        def handler(command):
            if command.startswith("newgame"):
                return [rcon_end(command), encode_packet(SERVER_NEWGAME)]
            return [rcon_end(command)]

        server = FakeAdminServer(rcon_handler=handler)
        client = self.connect_client(server, register_console=False)
        with self.assertRaises(AdminWaitTimeout) as ctx:
            client.new_game(seed=7, timeout=0.8)
        message = str(ctx.exception)
        self.assertIn("SERVER_WELCOME", message)
        self.assertIn("SERVER_NEWGAME", message)  # names what WAS seen

    def test_shutdown_graceful_quit(self) -> None:
        def handler(command):
            if command == "quit":
                return [rcon_end(command), encode_packet(SERVER_SHUTDOWN)]
            return [rcon_end(command)]

        class FakeProc:
            def wait(self, timeout=None):
                return 0

        server = FakeAdminServer(rcon_handler=handler)
        client = self.connect_client(server, register_console=False)
        result = client.shutdown(FakeProc())
        self.assertTrue(result["graceful"])
        self.assertIsNone(result["escalation"])
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("SERVER_SHUTDOWN", result["packets_after_quit"])

    def test_connect_diagnostic_when_listener_never_opens(self) -> None:
        config = AdminLifecycleConfig(port=1, password="x")  # nothing listens on port 1
        client = AdminClient(config)
        with self.assertRaises(AdminWaitTimeout) as ctx:
            client.connect(timeout=0.6)
        self.assertIn("never accepted a connection", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
