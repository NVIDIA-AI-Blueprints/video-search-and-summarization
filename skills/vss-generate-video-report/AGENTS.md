# AGENTS.md

## Scope

Applies to `vss-generate-video-report`, the skill for rendered video analysis
reports.

## Rules

- Choose Mode A for a specific clip/video and Mode B for incident or alert
  ranges.
- Do not route reports through VSS Agent `POST /generate`.
- Mode A output starts with exactly `# Video Analysis Report`.
- Mode B output starts with exactly `# Incident Range Report`.
- Empty Mode B ranges return the exact plain-text no-incidents sentence defined
  in `SKILL.md`; no markdown wrapper.
- Use `vss-query-analytics` for incident data and
  `vss-manage-video-io-storage` for VIOS clips when needed.

## Eval Behavior

- Do not silently switch modes when a dependency is missing.
- Preserve user-provided time ranges, scopes, report questions, and clip URLs.
