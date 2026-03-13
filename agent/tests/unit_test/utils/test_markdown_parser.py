# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for vss_agents/utils/markdown_parser.py."""

from vss_agents.utils.markdown_parser import parse_markdown_to_json
from vss_agents.utils.markdown_parser import parse_table_or_blocktext


class TestParseTable:
    """Tests for parse_table function."""

    def test_parse_simple_table(self):
        """Test parsing a simple markdown table."""
        lines = [
            "| Field | Value |",
            "|-------|-------|",
            "| Name | John |",
            "| Age | 30 |",
        ]
        result = parse_table_or_blocktext(lines)
        assert result == {"Name": "John", "Age": "30"}

    def test_parse_table_with_empty_lines(self):
        """Test parsing table with empty lines."""
        lines = [
            "| Field | Value |",
            "|-------|-------|",
            "",
            "| Name | John |",
            "",
        ]
        result = parse_table_or_blocktext(lines)
        assert result == {"Name": "John"}

    def test_parse_table_with_bold_text(self):
        """Test parsing table with bold text (asterisks stripped)."""
        lines = [
            "| **Field** | **Value** |",
            "|-----------|-----------|",
            "| **Name** | **John** |",
        ]
        result = parse_table_or_blocktext(lines)
        assert result == {"Name": "John"}

    def test_parse_table_with_multiple_values(self):
        """Test parsing table with multiple value columns."""
        lines = [
            "| Field | Value1 | Value2 |",
            "|-------|--------|--------|",
            "| Data | A | B |",
        ]
        result = parse_table_or_blocktext(lines)
        assert result == {"Data": ["A", "B"]}

    def test_parse_empty_table(self):
        """Test parsing empty table."""
        lines = []
        result = parse_table_or_blocktext(lines)
        assert result == {}

    def test_parse_table_skip_header(self):
        """Test that 'Field' header row is skipped."""
        lines = [
            "| Field | Value |",
            "|-------|-------|",
            "| Field | Test |",  # This should be skipped
        ]
        result = parse_table_or_blocktext(lines)
        assert result == {}

    def test_parse_blocktext_with_multiple_paras_time_and_image(self):
        """Test parsing multi-paragraph block removes time markers and images."""
        textblock = [
            "[00:05] Incident detected at main gate.",
            "",
            "<img src='example.jpg' />",
            " Additional context follows.",
            "",
            "[01:10] Secondary update after assessment.",
        ]
        result = parse_table_or_blocktext([], textblock)
        expected = (
            "Incident detected at main gate. " "Additional context follows. " "Secondary update after assessment."
        )
        # Ignore spacing differences introduced by line/paragraph joins
        assert result.replace(" ", "") == expected.replace(" ", "")


class TestParseMarkdownToJson:
    """Tests for parse_markdown_to_json function."""

    def test_parse_title(self):
        """Test parsing markdown title."""
        content = "# My Report Title"
        result = parse_markdown_to_json(content)
        assert result["title"] == "My Report Title"

    def test_parse_section_with_table(self):
        """Test parsing section with table."""
        content = """# Report

## Summary
| Field | Value |
|-------|-------|
| Status | Active |
| Count | 5 |
"""
        result = parse_markdown_to_json(content)
        assert result["title"] == "Report"
        assert result["Summary"] == {"Status": "Active", "Count": "5"}

    def test_parse_subsections(self):
        """Test parsing subsections within sections."""
        content = """# Report

## Main Section
### Subsection A
| Field | Value |
|-------|-------|
| Item | A |

### Subsection B
| Field | Value |
|-------|-------|
| Item | B |
"""
        result = parse_markdown_to_json(content)
        assert result["Main Section"]["Subsection A"] == {"Item": "A"}
        assert result["Main Section"]["Subsection B"] == {"Item": "B"}

    def test_parse_incident_snapshot_url(self):
        """Test parsing incident snapshot URL."""
        content = """# Report

**Incident Snapshot:** [View](http://example.com/snapshot.jpg)
"""
        result = parse_markdown_to_json(content)
        assert result["Resources"]["Incident Snapshot"] == "http://example.com/snapshot.jpg"

    def test_parse_incident_video_url(self):
        """Test parsing incident video URL."""
        content = """# Report

**Incident Video:** [View](http://example.com/video.mp4)
"""
        result = parse_markdown_to_json(content)
        assert result["Resources"]["Incident Video"] == "http://example.com/video.mp4"

    def test_parse_incident_url_plain(self):
        """Test parsing incident URL on next line (plain format)."""
        content = """# Report

**Incident Snapshot:**
http://example.com/snapshot.jpg
"""
        result = parse_markdown_to_json(content)
        assert result["Resources"]["Incident Snapshot"] == "http://example.com/snapshot.jpg"

    def test_parse_multiple_sections(self):
        """Test parsing multiple sections."""
        content = """# Report

## Section 1
| Field | Value |
|-------|-------|
| A | 1 |

## Section 2
| Field | Value |
|-------|-------|
| B | 2 |
"""
        result = parse_markdown_to_json(content)
        assert result["Section 1"] == {"A": "1"}
        assert result["Section 2"] == {"B": "2"}

    def test_parse_empty_content(self):
        """Test parsing empty content."""
        content = ""
        result = parse_markdown_to_json(content)
        assert result == {}

    def test_parse_content_without_tables(self):
        """Test parsing content without any tables."""
        content = """# Title

## Section
Some text without tables.
"""
        result = parse_markdown_to_json(content)
        assert result["title"] == "Title"

    def test_parse_subsection_with_table_then_new_section(self):
        """Test parsing subsection table followed by new section (covers lines 50-52)."""
        content = """# Report

## Section 1
### Subsection A
| Field | Value |
|-------|-------|
| Key | Val |

## Section 2
| Field | Value |
|-------|-------|
| Other | Data |
"""
        result = parse_markdown_to_json(content)
        assert result["Section 1"]["Subsection A"] == {"Key": "Val"}
        assert result["Section 2"] == {"Other": "Data"}

    def test_parse_subsection_without_prior_section_dict(self):
        """Test parsing subsection when section not yet a dict (covers line 63, 66-67)."""
        content = """# Report

## Main Section
### Sub A
| Field | Value |
|-------|-------|
| A | 1 |

### Sub B
| Field | Value |
|-------|-------|
| B | 2 |
"""
        result = parse_markdown_to_json(content)
        assert "Main Section" in result
        assert result["Main Section"]["Sub A"] == {"A": "1"}
        assert result["Main Section"]["Sub B"] == {"B": "2"}

    def test_parse_incident_video_url_plain(self):
        """Test parsing incident video URL on next line (covers lines 96-100)."""
        content = """# Report

**Incident Video:**
https://example.com/video.mp4
"""
        result = parse_markdown_to_json(content)
        assert result["Resources"]["Incident Video"] == "https://example.com/video.mp4"

    def test_parse_subsection_table_at_end(self):
        """Test parsing subsection table at end of content (covers line 108)."""
        content = """# Report

## Main
### Details
| Field | Value |
|-------|-------|
| Final | Item |
"""
        result = parse_markdown_to_json(content)
        assert result["Main"]["Details"] == {"Final": "Item"}

    def test_section_not_in_result_when_new_section_starts(self):
        """Test edge case where section not in result when new ## starts (covers line 51)."""
        # This case requires: subsection table, then new section, where current_section wasn't added
        content = """# Report

## Section1
### SubA
| Field | Value |
|-------|-------|
| X | Y |

## Section2
| Field | Value |
|-------|-------|
| A | B |
"""
        result = parse_markdown_to_json(content)
        assert result["Section1"]["SubA"] == {"X": "Y"}
        assert result["Section2"] == {"A": "B"}

    def test_section_table_without_subsection_when_new_subsection_starts(self):
        """Test edge case for section table when new subsection starts (covers lines 66-67)."""
        content = """# Report

## Summary
| Field | Value |
|-------|-------|
| Status | Active |

### Details
| Field | Value |
|-------|-------|
| Item | Value |
"""
        result = parse_markdown_to_json(content)
        # Parser behavior: first table is processed when ### is encountered
        # Lines 66-67 make the section a dict if it wasn't before
        assert "Summary" in result
        assert "Details" in result["Summary"]
        assert result["Summary"]["Details"] == {"Item": "Value"}

    def test_consecutive_subsections_with_tables(self):
        """Test consecutive subsections with tables (covers line 63)."""
        content = """# Report

## Parent
### FirstSub
| Field | Value |
|-------|-------|
| A | 1 |

### SecondSub
| Field | Value |
|-------|-------|
| B | 2 |

### ThirdSub
| Field | Value |
|-------|-------|
| C | 3 |
"""
        result = parse_markdown_to_json(content)
        assert result["Parent"]["FirstSub"] == {"A": "1"}
        assert result["Parent"]["SecondSub"] == {"B": "2"}
        assert result["Parent"]["ThirdSub"] == {"C": "3"}
