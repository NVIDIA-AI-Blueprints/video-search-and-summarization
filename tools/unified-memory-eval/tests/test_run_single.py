#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main

from scripts.run_single import discover_question_files


class DiscoverQuestionFilesTest(TestCase):
    def test_discovers_all_eval_files_by_default(self) -> None:
        with TemporaryDirectory() as temp_dir:
            questions_dir = Path(temp_dir)
            second = questions_dir / "video_2_eval.tsv"
            first = questions_dir / "video_1_eval.tsv"
            ignored = questions_dir / "cross-incidents.tsv"
            for path in (second, first, ignored):
                path.touch()

            self.assertEqual(discover_question_files(questions_dir), [first, second])

    def test_resolves_selected_filename_from_questions_dir(self) -> None:
        with TemporaryDirectory() as temp_dir:
            questions_dir = Path(temp_dir)
            selected = questions_dir / "video_2_eval.tsv"
            selected.touch()

            self.assertEqual(discover_question_files(questions_dir, Path(selected.name)), [selected])

    def test_accepts_explicit_question_file_path(self) -> None:
        with TemporaryDirectory() as temp_dir:
            selected = Path(temp_dir) / "video_2_eval.tsv"
            selected.touch()

            self.assertEqual(discover_question_files(Path("unused"), selected), [selected])

    def test_rejects_non_eval_tsv(self) -> None:
        with TemporaryDirectory() as temp_dir:
            selected = Path(temp_dir) / "cross-incidents.tsv"
            selected.touch()

            with self.assertRaisesRegex(ValueError, "must end with _eval.tsv"):
                discover_question_files(Path(temp_dir), selected)

    def test_reports_missing_selected_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(FileNotFoundError, "Question file not found"):
                discover_question_files(Path(temp_dir), Path("missing_eval.tsv"))


if __name__ == "__main__":
    main()
