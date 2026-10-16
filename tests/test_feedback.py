from __future__ import annotations

import unittest
from pathlib import Path

from openttd_mcp.feedback import (
    FEEDBACK_KINDS,
    append_feedback,
    load_feedback,
    validate_report_feedback,
)
from tests.support import TempDirTestCase, feedback_args


class ReportFeedbackValidationTests(TempDirTestCase):
    def test_validate_accepts_every_kind_and_normalizes_strings(self):
        for kind in FEEDBACK_KINDS:
            entry = validate_report_feedback(
                {
                    "kind": kind,
                    "description": "  what I wanted  ",
                    "why_it_matters": "how this would affect my play",
                    "current_blocker": "what I could not determine",
                    "extra_field": "dropped",
                }
            )
            self.assertEqual(entry["kind"], kind)
            self.assertEqual(entry["description"], "what I wanted")
            self.assertEqual(entry["schema_version"], 1)
            self.assertNotIn("extra_field", entry)

    def test_validate_rejects_unknown_kind(self):
        with self.assertRaises(ValueError):
            validate_report_feedback(
                feedback_args(
                    kind="request_candidates",
                    description="x",
                    why_it_matters="y",
                    current_blocker="z",
                )
            )

    def test_validate_rejects_missing_or_empty_required_strings(self):
        base = feedback_args(
            kind="missing_action",
            description="d",
            why_it_matters="w",
            current_blocker="b",
        )
        for field in ("description", "why_it_matters", "current_blocker"):
            payload = dict(base)
            payload[field] = "   "
            with self.assertRaises(ValueError):
                validate_report_feedback(payload)
            payload.pop(field)
            with self.assertRaises(ValueError):
                validate_report_feedback(payload)

    def test_validate_rejects_non_object_payload(self):
        with self.assertRaises(ValueError):
            validate_report_feedback("not a dict")  # type: ignore[arg-type]

    def test_append_and_load_round_trip(self):
        path = self.tmp_path / "feedback.jsonl"
        first = validate_report_feedback(
            feedback_args(description="d1", why_it_matters="w1", current_blocker="b1")
        )
        second = validate_report_feedback(
            feedback_args(
                kind="strategic_note",
                description="d2",
                why_it_matters="w2",
                current_blocker="b2",
            )
        )
        append_feedback(path, first)
        append_feedback(path, second)

        entries = load_feedback(path)
        self.assertEqual(
            [entry["kind"] for entry in entries],
            ["missing_observation", "strategic_note"],
        )
        self.assertEqual(entries[0], first)
        self.assertEqual(entries[1], second)

    def test_load_feedback_on_missing_file_is_empty(self):
        self.assertEqual(load_feedback(Path("/nonexistent/feedback.jsonl")), [])


if __name__ == "__main__":
    unittest.main()
