from __future__ import annotations

from openttd_mcp import server
from openttd_mcp.log_tailer import tail_fragment_since, tail_lines_since
from tests.support import TempDirTestCase


class WholeLineLogTailerTests(TempDirTestCase):
    def test_only_line_feed_splits_records(self) -> None:
        path = self.tmp_path / "openttd.log"
        separators = "\v\f\x1c\x1d\x1e\x85\u2028\u2029\r"
        record = separators.join(("start", "middle", "end"))
        path.write_text(record + "\n")

        self.assertEqual(tail_lines_since(path, 0), ([record], path.stat().st_size))

    def test_carriage_return_line_feed_keeps_legacy_output_and_lone_cr(self) -> None:
        path = self.tmp_path / "openttd.log"
        path.write_bytes(b"first\r\nsecond\rpart\n")

        self.assertEqual(tail_lines_since(path, 0), (["first", "second\rpart"], 19))

    def test_unicode_separators_inside_result_json_reach_the_parser_whole(self) -> None:
        path = self.tmp_path / "openttd.log"
        detail = "next-line\x85line-separator\u2028paragraph-separator\u2029end"
        path.write_text(f'MCP_RESULT|turn|{{"detail":"{detail}"}}\n')

        lines, _ = tail_lines_since(path, 0)

        self.assertEqual(len(lines), 1)
        self.assertEqual(server.parse_result_line(lines[0], "turn"), {"detail": detail})

    def test_unterminated_fragment_is_parseable_and_non_consuming(self) -> None:
        path = self.tmp_path / "openttd.log"
        path.write_text('done\nMCP_RESULT|turn|{"ok":true}')
        lines, offset = tail_lines_since(path, 0)

        self.assertEqual(lines, ["done"])
        self.assertEqual(offset, 5)
        self.assertEqual(
            tail_fragment_since(path, offset),
            'MCP_RESULT|turn|{"ok":true}',
        )
        self.assertEqual(tail_lines_since(path, offset), ([], offset))

        with path.open("a") as handle:
            handle.write("\n")
        self.assertEqual(
            tail_lines_since(path, offset),
            (['MCP_RESULT|turn|{"ok":true}'], path.stat().st_size),
        )
        self.assertEqual(tail_fragment_since(path, 0), "")
        self.assertEqual(tail_fragment_since(path, path.stat().st_size), "")

    def test_shorter_file_resets_but_same_size_rewrite_is_not_detectable(
        self,
    ) -> None:
        path = self.tmp_path / "openttd.log"
        self.assertEqual(tail_lines_since(path, 12), ([], 12))
        self.assertEqual(tail_fragment_since(path, 12), "")

        path.write_text("old-one\n")
        _, offset = tail_lines_since(path, 0)
        self.assertEqual(tail_lines_since(path, offset), ([], offset))

        path.write_text("new\n")
        self.assertEqual(tail_lines_since(path, offset), (["new"], 4))

        path.write_text("new-one\n")
        self.assertEqual(path.stat().st_size, offset)
        self.assertEqual(tail_lines_since(path, offset), ([], offset))

    def test_byte_offsets_preserve_split_multibyte_and_replace_invalid_records(
        self,
    ) -> None:
        path = self.tmp_path / "openttd.log"
        payload = "café\n".encode()
        path.write_bytes(payload[:4])
        self.assertEqual(tail_lines_since(path, 0), ([], 0))

        with path.open("ab") as handle:
            handle.write(payload[4:] + b"bad\xff\xfebyte\n")

        self.assertEqual(
            tail_lines_since(path, 0),
            (["café", "bad��byte"], path.stat().st_size),
        )

    def test_fragment_peek_is_bounded_without_consuming_the_record(self) -> None:
        path = self.tmp_path / "openttd.log"
        path.write_text("complete\nunterminated")
        offset = len("complete\n")

        self.assertEqual(tail_fragment_since(path, offset, max_bytes=5), "unter")
        self.assertEqual(tail_fragment_since(path, 0), "unterminated")
        self.assertEqual(tail_lines_since(path, offset), ([], offset))
        self.assertEqual(tail_fragment_since(path, 999), "")

    def test_truncate_then_grow_can_skip_a_new_generation_prefix(self) -> None:
        path = self.tmp_path / "openttd.log"
        path.write_text("old-one\n")
        _, offset = tail_lines_since(path, 0)

        path.write_text("new-one\nnew-two\n")

        self.assertEqual(tail_lines_since(path, offset), (["new-two"], 16))
