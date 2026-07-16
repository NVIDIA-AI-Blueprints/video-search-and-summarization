#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import patch

from scripts.run_single import (
    answer_question,
    discover_question_files,
    parse_category,
    read_question_file,
    resolve_video_name,
    run_openclaw_json,
    summarize_single_raw,
)


class DiscoverQuestionFilesTest(TestCase):
    def test_discovers_all_eval_files_by_default(self) -> None:
        with TemporaryDirectory() as temp_dir:
            questions_dir = Path(temp_dir)
            second = questions_dir / "video_2_eval.json"
            first = questions_dir / "video_1_eval.json"
            ignored = questions_dir / "cross-incidents.json"
            for path in (second, first, ignored):
                path.touch()

            self.assertEqual(discover_question_files(questions_dir), [first, second])

    def test_accepts_selected_question_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            selected = Path(temp_dir) / "video_2_eval.json"
            selected.touch()

            self.assertEqual(discover_question_files(question_file=selected), [selected])

    def test_accepts_explicit_question_file_path(self) -> None:
        with TemporaryDirectory() as temp_dir:
            selected = Path(temp_dir) / "video_2_eval.json"
            selected.touch()

            self.assertEqual(discover_question_files(question_file=selected), [selected])

    def test_accepts_explicit_non_eval_json(self) -> None:
        with TemporaryDirectory() as temp_dir:
            selected = Path(temp_dir) / "new-body-cam1.json"
            selected.touch()

            self.assertEqual(discover_question_files(question_file=selected), [selected])

    def test_rejects_explicit_non_json_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            selected = Path(temp_dir) / "questions.tsv"
            selected.touch()

            with self.assertRaisesRegex(ValueError, "must be JSON"):
                discover_question_files(question_file=selected)

    def test_reports_missing_selected_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(FileNotFoundError, "Question file not found"):
                discover_question_files(question_file=Path(temp_dir) / "missing_eval.json")

    def test_discovers_custom_metadata_file_and_ignores_cross_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            question_dir = Path(temp_dir)
            custom = question_dir / "custom.json"
            custom.write_text('{"video_id":"video","questions":[]}', encoding="utf-8")
            (question_dir / "cross.json").write_text(
                '[{"scenario_id":"s1","turn_id":1,"family":"locator","question":"q"}]',
                encoding="utf-8",
            )

            self.assertEqual(discover_question_files(question_dir=question_dir), [custom])

    def test_rejects_mixed_file_and_directory_selectors(self) -> None:
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            discover_question_files(Path("questions"), Path("question.json"))


class VideoNameTest(TestCase):
    def test_derives_video_name_from_eval_filename(self) -> None:
        self.assertEqual(resolve_video_name(Path("body_cam_eval.json")), "body_cam")

    def test_uses_embedded_video_id(self) -> None:
        self.assertEqual(resolve_video_name(Path("new-body-cam1.json"), "body_cam"), "body_cam")

    def test_requires_metadata_for_custom_filename(self) -> None:
        with self.assertRaisesRegex(ValueError, "must contain video_id and questions"):
            resolve_video_name(Path("new-body-cam1.json"))

    def test_reads_custom_file_metadata_and_questions(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "new-body-cam1.json"
            path.write_text(
                '{"video_id":"body_cam","questions":[{"qid":1}]}',
                encoding="utf-8",
            )

            video_id, rows = read_question_file(path)

            self.assertEqual(video_id, "body_cam")
            self.assertEqual(rows, [{"qid": 1}])


class CategoryMetricsTest(TestCase):
    def test_validates_category(self) -> None:
        self.assertEqual(parse_category(" Temporal "), "temporal")
        with self.assertRaisesRegex(ValueError, "category must be one of"):
            parse_category("broad")

    def test_summarizes_each_category(self) -> None:
        rows = [
            {
                "category": "within_event",
                "answer_accuracy": 1,
                "exact_evidence_match": 1,
                "expected_event_ids": [1],
                "predicted_event_ids": [1],
            },
            {
                "category": "temporal",
                "answer_accuracy": 0,
                "exact_evidence_match": 0,
                "expected_event_ids": [2],
                "predicted_event_ids": [],
            },
        ]

        metrics = summarize_single_raw(rows)

        self.assertEqual(metrics["categories"]["within_event"]["answer_accuracy"], 1.0)
        self.assertEqual(metrics["categories"]["temporal"]["answer_accuracy"], 0.0)


class OpenClawJsonTest(TestCase):
    @patch("scripts.run_single.run_openclaw")
    def test_retries_malformed_json(self, mocked_run_openclaw) -> None:
        mocked_run_openclaw.side_effect = [
            ("cleanup message", 10, 1),
            ('{"ready": true}', 20, 1),
        ]

        parsed, latency_ms, tool_calls = run_openclaw_json(
            "message", "session", "model", 30, Path("openclaw.log")
        )

        self.assertEqual(parsed, {"ready": True})
        self.assertEqual(latency_ms, 30)
        self.assertEqual(tool_calls, 2)

    @patch("scripts.run_single.run_openclaw_json")
    def test_question_prompt_includes_category_and_temporal_anchor_rule(
        self, mocked_run_openclaw_json
    ) -> None:
        mocked_run_openclaw_json.return_value = (
            {"answer": "Next action.", "cited_event_ids": [1, 2], "citation_reason": "Sequence."},
            10,
            1,
        )

        answer_question("11", "temporal", "What happens next?", "session", "model", 30, Path("log"))

        prompt = mocked_run_openclaw_json.call_args.args[0]
        self.assertIn("Category: temporal", prompt)
        self.assertIn("For within_event questions, cite the single event", prompt)
        self.assertIn("For entity_relational questions, cite the smallest event set", prompt)
        self.assertIn("For temporal questions, cite the anchor event", prompt)


if __name__ == "__main__":
    main()
