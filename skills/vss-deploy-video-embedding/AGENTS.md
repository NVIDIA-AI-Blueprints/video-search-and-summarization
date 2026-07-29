# AGENTS.md

## Scope

Applies to `vss-deploy-video-embedding`, the RT-Embed standalone service skill.

## Rules

- Use this skill for video/text embedding service deployment and API calls, not
  for archive search orchestration unless the request is specifically about the
  embedding microservice.
- Verify model, endpoint, Redis/Kafka integration, and GPU requirements from
  the skill references before launch.
- Keep ingest/search workflows routed through `vss-search-archive` when the
  user asks for search results rather than embedding service operation.

## Eval Behavior

- Report service health, API response shape, and embedding/search readiness
  evidence named by the spec.
