# AGENTS.md

## Scope

Applies to `vss-search-archive`, the skill for archive ingestion, deletion, and
natural-language video search.

## Rules

- Use this skill for retrieval across an archive, not for one-off Q&A,
  summaries, dense captioning, or analytics incident lookup.
- Verify the search profile and endpoint contract before running search or
  ingestion.
- For Docker Compose, run the host-side `vss search` CLI from the checkout via
  `uv`; do not `docker exec` into distroless containers.
- For Kubernetes, use the public VSS Agent Ingress through `VSS_PUBLIC_URL`;
  do not create port-forwards or guess service names.
- Preserve source names and returned matches exactly in the final answer.

## Eval Behavior

- If a prerequisite deploy is required and pre-authorized, use
  `vss-deploy-profile` for the search profile, then return to this skill.
- Prefer observable proof: search query, source id/name, match count, and
  endpoint used.
