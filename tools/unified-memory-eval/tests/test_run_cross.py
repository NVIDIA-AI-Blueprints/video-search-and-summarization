#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main

from scripts.run_cross import discover_question_files, group_scenarios, load_question_files


VIDEO_ID = "video-1"


def legacy_cross_row(scenario_id: str) -> dict[str, object]:
    return {
        "scenario_id": scenario_id,
        "turn_id": 1,
        "family": "cross_video_locator",
        "question": "Which video?",
    }


def canonical_questions(video_id: str = VIDEO_ID) -> dict[str, object]:
    questions = []
    qid = 0
    for category in ("within_event", "entity_relational", "temporal"):
        for _ in range(5):
            qid += 1
            questions.append(
                {
                    "qid": qid,
                    "category": category,
                    "question": f"Canonical question {qid}?",
                    "expected_answer_target": f"Canonical answer {qid}.",
                    "expected_event_ids": [qid],
                }
            )
    return {"video_id": video_id, "questions": questions}


def cross_manifest(
    source: str = "single.json",
    cross_video_questions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "scenarios": [
            {
                "scenario_id": "s1",
                "incident_id": "incident-1",
                "focal_video_id": VIDEO_ID,
                "single_question_source": source,
                "locator": {
                    "question": "Which video?",
                    "expected_answer_target": "Video one.",
                    "expected_video_ids": [VIDEO_ID],
                    "expected_event_ids": {VIDEO_ID: [1]},
                },
                "cross_video_questions": cross_video_questions or [],
            }
        ],
    }


def write_manifest_fixture(
    root: Path,
    *,
    canonical: dict[str, object] | None = None,
    manifest: dict[str, object] | None = None,
) -> Path:
    (root / "single.json").write_text(
        json.dumps(canonical or canonical_questions()), encoding="utf-8"
    )
    manifest_path = root / "cross.json"
    manifest_path.write_text(json.dumps(manifest or cross_manifest()), encoding="utf-8")
    return manifest_path


class DiscoverQuestionFilesTest(TestCase):
    def test_discovers_legacy_and_manifest_cross_question_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            question_dir = Path(temp_dir)
            legacy = question_dir / "legacy-cross.json"
            legacy.write_text(json.dumps([legacy_cross_row("legacy")]), encoding="utf-8")
            manifest = write_manifest_fixture(question_dir)
            (question_dir / "single_eval.json").write_text(
                '[{"qid":1,"question":"What happened?"}]', encoding="utf-8"
            )
            (question_dir / "custom-single.json").write_text(
                '{"video_id":"video","questions":[]}', encoding="utf-8"
            )

            self.assertEqual(
                discover_question_files(question_dir=question_dir), [manifest, legacy]
            )

    def test_accepts_selected_question_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            selected = Path(temp_dir) / "cross.json"
            selected.touch()

            self.assertEqual(discover_question_files(question_file=selected), [selected])

    def test_rejects_mixed_file_and_directory_selectors(self) -> None:
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            discover_question_files(Path("questions"), Path("cross.json"))


class LoadQuestionFilesTest(TestCase):
    def test_example_manifest_expands_five_sixteen_turn_scenarios(self) -> None:
        manifest_path = (
            Path(__file__).resolve().parents[1]
            / "examples/questions/cross-incidents/cross-incidents.json"
        )

        grouped = group_scenarios(load_question_files([manifest_path]))

        self.assertEqual(set(grouped), {"s1", "s2", "s3", "s4", "s5"})
        self.assertEqual({scenario_id: len(rows) for scenario_id, rows in grouped.items()}, {
            "s1": 16,
            "s2": 16,
            "s3": 16,
            "s4": 16,
            "s5": 16,
        })

    def test_combines_legacy_files_with_distinct_scenarios(self) -> None:
        with TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.json"
            second = Path(temp_dir) / "second.json"
            first.write_text(json.dumps([legacy_cross_row("s1")]), encoding="utf-8")
            second.write_text(json.dumps([legacy_cross_row("s2")]), encoding="utf-8")

            self.assertEqual(
                load_question_files([first, second]),
                [legacy_cross_row("s1"), legacy_cross_row("s2")],
            )

    def test_rejects_scenario_ids_split_across_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.json"
            second = Path(temp_dir) / "second.json"
            first.write_text(json.dumps([legacy_cross_row("s1")]), encoding="utf-8")
            second.write_text(json.dumps([legacy_cross_row("s1")]), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "appears in both"):
                load_question_files([first, second])

    def test_expands_locator_and_canonical_questions_from_relative_source(self) -> None:
        with TemporaryDirectory() as temp_dir:
            manifest_path = write_manifest_fixture(Path(temp_dir))

            rows = load_question_files([manifest_path])
            grouped = group_scenarios(rows)

            self.assertEqual(len(rows), 16)
            self.assertEqual([row["turn_id"] for row in grouped["s1"]], list(range(1, 17)))
            self.assertEqual(rows[0]["turn_kind"], "locator")
            self.assertEqual(rows[1]["turn_kind"], "canonical_followup")
            self.assertEqual(rows[1]["source_qid"], 1)
            self.assertEqual(rows[1]["family"], "within_event")
            self.assertEqual(rows[1]["expected_video_ids"], [VIDEO_ID])
            self.assertEqual(rows[1]["expected_event_ids"], {VIDEO_ID: [1]})
            self.assertTrue(rows[1]["question"].startswith("For that same video:"))

    def test_appends_cross_video_evidence_joins(self) -> None:
        cross_question = {
            "cqid": 1,
            "reasoning_axis": "scene_correlation",
            "question": "What detail requires both videos?",
            "expected_answer_target": "A joined answer.",
            "expected_video_ids": [VIDEO_ID, "video-2"],
            "expected_event_ids": {VIDEO_ID: [3], "video-2": [9]},
        }
        with TemporaryDirectory() as temp_dir:
            manifest_path = write_manifest_fixture(
                Path(temp_dir),
                manifest=cross_manifest(cross_video_questions=[cross_question]),
            )

            rows = load_question_files([manifest_path])

            self.assertEqual(len(rows), 17)
            self.assertEqual(rows[-1]["turn_id"], 17)
            self.assertEqual(rows[-1]["turn_kind"], "cross_video_evidence_join")
            self.assertEqual(rows[-1]["reasoning_axis"], "scene_correlation")
            self.assertEqual(rows[-1]["expected_event_ids"], {VIDEO_ID: [3], "video-2": [9]})

    def test_rejects_cross_question_that_does_not_require_two_videos(self) -> None:
        cross_question = {
            "cqid": 1,
            "question": "What happened?",
            "expected_answer_target": "Something happened.",
            "expected_video_ids": [VIDEO_ID],
            "expected_event_ids": {VIDEO_ID: [1]},
        }
        with TemporaryDirectory() as temp_dir:
            manifest_path = write_manifest_fixture(
                Path(temp_dir),
                manifest=cross_manifest(cross_video_questions=[cross_question]),
            )

            with self.assertRaisesRegex(ValueError, "at least two expected videos"):
                load_question_files([manifest_path])

    def test_rejects_cross_question_without_evidence_for_every_video(self) -> None:
        cross_question = {
            "cqid": 1,
            "question": "What requires both videos?",
            "expected_answer_target": "A joined answer.",
            "expected_video_ids": [VIDEO_ID, "video-2"],
            "expected_event_ids": {VIDEO_ID: [1]},
        }
        with TemporaryDirectory() as temp_dir:
            manifest_path = write_manifest_fixture(
                Path(temp_dir),
                manifest=cross_manifest(cross_video_questions=[cross_question]),
            )

            with self.assertRaisesRegex(ValueError, "missing expected videos"):
                load_question_files([manifest_path])

    def test_rejects_unbalanced_canonical_question_source(self) -> None:
        unbalanced = canonical_questions()
        unbalanced["questions"][0]["category"] = "temporal"  # type: ignore[index]
        with TemporaryDirectory() as temp_dir:
            manifest_path = write_manifest_fixture(Path(temp_dir), canonical=unbalanced)

            with self.assertRaisesRegex(ValueError, "canonical category counts"):
                load_question_files([manifest_path])

    def test_rejects_canonical_source_for_another_video(self) -> None:
        with TemporaryDirectory() as temp_dir:
            manifest_path = write_manifest_fixture(
                Path(temp_dir), canonical=canonical_questions("video-2")
            )

            with self.assertRaisesRegex(ValueError, "does not match focal_video_id"):
                load_question_files([manifest_path])


class GroupScenariosTest(TestCase):
    def test_legacy_scenario_can_have_dynamic_consecutive_turn_count(self) -> None:
        rows = [
            {**legacy_cross_row("s1"), "turn_id": turn_id}
            for turn_id in range(1, 4)
        ]

        grouped = group_scenarios(rows)

        self.assertEqual(len(grouped["s1"]), 3)
        self.assertEqual(grouped["s1"][0]["turn_kind"], "locator")
        self.assertEqual(grouped["s1"][1]["turn_kind"], "legacy_followup")

    def test_rejects_nonconsecutive_turn_ids(self) -> None:
        rows = [legacy_cross_row("s1"), {**legacy_cross_row("s1"), "turn_id": 3}]

        with self.assertRaisesRegex(ValueError, "consecutive turn IDs"):
            group_scenarios(rows)


if __name__ == "__main__":
    main()
