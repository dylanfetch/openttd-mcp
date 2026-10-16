"""Whole-line reads from an append-only log file."""

from __future__ import annotations

from pathlib import Path


def tail_lines_since(path: Path, offset: int) -> tuple[list[str], int]:
    """Return complete line-feed-terminated records past a byte offset.

    A final unterminated record is left unread, so a later append delivers it
    whole. Invalid Unicode Transformation Format 8-bit (UTF-8) bytes are
    replaced. A carriage return immediately before a line feed is removed for
    compatibility with carriage-return–line-feed logs; every other character,
    including Unicode line separators and lone carriage returns, remains in
    its record.

    The active-log contract is append-only. A shorter file resets the offset,
    but an in-place rewrite or replacement at least as large as ``offset`` is
    not detectable from a byte offset and file size alone.
    """
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            if size < offset:
                offset = 0
            if size == offset:
                return [], offset
            handle.seek(offset)
            data = handle.read(size - offset)
    except FileNotFoundError:
        return [], offset

    end = data.rfind(b"\n")
    if end < 0:
        return [], offset
    consumed = data[: end + 1]
    decoded = consumed.decode("utf-8", errors="replace")
    lines = decoded.split("\n")[:-1]
    return [line.removesuffix("\r") for line in lines], offset + len(consumed)


def tail_fragment_since(
    path: Path, offset: int, *, max_bytes: int | None = None
) -> str:
    """Peek at the final unterminated record past ``offset`` without consuming it."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            if size <= offset:
                return ""
            handle.seek(offset)
            data = handle.read(size - offset)
    except FileNotFoundError:
        return ""

    fragment = data[data.rfind(b"\n") + 1 :]
    if max_bytes is not None:
        fragment = fragment[:max_bytes]
    return fragment.decode("utf-8", errors="replace")
