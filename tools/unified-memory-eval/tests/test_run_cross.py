#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main

from scripts.run_cross import discover_question_files, load_question_files


def cross_row(scenario_id: str) -> dict[str, object]:
    return {
        "scenario_id": scenario_id,
        "turn_id": 1,
        "family": "cross_video_locator",
        "question": "Which incident?",
    }


class DiscoverQuestionFilesTest(TestCase):
    def test_discovers_only_cross_question_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            question_dir = Path(temp_dir)
            cross = question_dir / "cross.json"
            cross.write_text(json.dumps([cross_row("s1")]), encoding="utf-8")
            (question_dir / "single_eval.json").write_text(
                '[{"qid":1,"question":"What happened?"}]', encoding="utf-8"
            )
            (question_dir / "custom-single.json").write_text(
                '{"video_id":"video","questions":[]}', encoding="utf-8"
            )

            self.assertEqual(discover_question_files(question_dir=question_dir), [cross])

    def test_accepts_selected_question_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            selected = Path(temp_dir) / "cross.json"
            selected.touch()

            self.assertEqual(discover_question_files(question_file=selected), [selected])

    def test_rejects_mixed_file_and_directory_selectors(self) -> None:
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            discover_question_files(Path("questions"), Path("cross.json"))


class LoadQuestionFilesTest(TestCase):
    def test_combines_files_with_distinct_scenarios(self) -> None:
        with TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.json"
            second = Path(temp_dir) / "second.json"
            first.write_text(json.dumps([cross_row("s1")]), encoding="utf-8")
            second.write_text(json.dumps([cross_row("s2")]), encoding="utf-8")

            self.assertEqual(load_question_files([first, second]), [cross_row("s1"), cross_row("s2")])

    def test_rejects_scenario_ids_split_across_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.json"
            second = Path(temp_dir) / "second.json"
            first.write_text(json.dumps([cross_row("s1")]), encoding="utf-8")
            second.write_text(json.dumps([cross_row("s1")]), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "appears in both"):
                load_question_files([first, second])


if __name__ == "__main__":
    main()
