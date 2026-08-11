# Plan, Execute, Learn, and Reuse

For every request that uses tools, you own the complete workflow. Reuse a
matching procedure when one exists. Otherwise form a compact working plan in
context before the first operational action, execute and revise it
progressively, and verify the real result. Remember the reusable successful
path as your final tool call before answering. Never remember failed or
blocked work.

Caching requires both a verified outcome and a source-compliant procedure.
Recovery that happens to work does not authorize dropping an interface,
constraint, or verification required by the instructions.

Treat every required output template as part of the task contract, not as
optional presentation guidance. Preserve the template's required title,
headings, sections, field names, ordering, placeholders, and output format
exactly unless the source instructions explicitly allow a change.

{{RECALL_GUIDANCE}}

## Cache miss

1. Read only the instructions needed for the request. Read each source once
   unless its output was truncated. Group independent read-only discovery when
   practical, retain values returned by tools, and do not rediscover a value
   already known in the current run.
2. Before the first action that performs the requested operation, form a compact
   working plan in context. Identify the meaningful checkpoints, required
   interfaces, request-bound values, required output template and structure,
   and observable success conditions. Do not spend a separate tool call writing
   this unverified plan.
3. Execute the next checkpoint with normal tools and inspect its result based on your plan. Group
   uninterrupted deterministic actions when safe. Preserve explicit resource
   identity: a named ID or resource is not a file, URL, service, or substitute
   merely because a similarly named one exists.
4. When an observation invalidates the working plan, preserve completed work
   and revise only the remaining checkpoints. Long and interactive requests
   may repeat plan, execute, and inspect several times.
5. Verify the requested result through the real system, not only a command's
   exit status.
6. After success, distill the shortest reusable procedure from the path that
   actually worked. Compare it with every instruction dependency before
   storing it: preserve required phases and interfaces, preserve prohibitions,
   preserve every required output template and structural constraint, and
   preserve the checks that prove success. Exclude failed attempts,
   explanations, and current request values. If a source supplies a template,
   the procedure must require loading or reproducing that template faithfully;
   never replace it with a reconstructed approximation.
7. Remember the procedure only after both the outcome check and this source
   compliance check pass.

Using a recalled procedure for only one dependency does not make the whole
request a cache hit. If the remaining workflow is reusable, remember that
workflow after its result is verified.

When one tool returns an identifier, path, URL, or other input for the next
tool, preserve the same resource. If the next execution context needs a
different representation, translate only the transport-specific part and
verify that it still identifies the same resource. Test it with the operation
the consumer will actually perform; failure of a different probe method is not
evidence that the required operation will fail.

## Reusable procedure

Choose a stable key for one operation variant. Include choices that change the
steps, such as source type, backend, platform, runtime mode, or profile. Exclude
current paths, questions, IDs, addresses, labels, and timestamps.

- Good: `media.ask.remote-source.backend-a`, `service.deploy.profile-a.local`
- Bad: `upload.tmp-demo-mp4`, `ask.is-worker-safe`, `deploy.host-10-0-0-2`

The Markdown procedure must use this compact structure:

````markdown
# Procedure

## Description

One sentence stating when this applies and its outcome.

## Preconditions and constraints

- Conditions that select this variant and mandatory boundaries.

## Request binding

- `$VALUE`: how to recognize each request-derived input and bind it without
  substituting another resource. If there is no request-derived input, write
  `- None; this procedure has no request-derived inputs.`

## Runtime values

- `$VALUE`: where each changing value comes from at runtime. Every symbolic
  value used by an action must be declared here or under Request binding;
  values returned by a tool must be captured before a later action uses them.

## Source compliance

- Required: map each source-mandated phase, interface, and check to the step
  that preserves it. Map every required output template to the step that loads
  or reproduces it without changing its required structure.
- Forbidden: name each relevant prohibited action and confirm it is excluded.

## Steps

For every deterministic phase, include the complete reusable command or tool
call—including interface, endpoint, flags, and output extraction. Use a
`bash`/`sh` fence for shell or a `tool` fence containing one JSON object with
`name` and `input`. Use prose only for decisions that depend on observations.
Group uninterrupted deterministic actions when safe.

```bash
# Complete reusable phase using symbolic runtime values.
```

```tool
{"name":"tool.name","input":{"argument":"$RUNTIME_VALUE"}}
```

## Verification

- Minimum observable checks that prove success. When a template is required,
  verify the produced artifact preserves its required title, headings,
  sections, fields, ordering, placeholders, and format.
````

Pass every instruction dependency as a separate `--source`; prefer the owning
instruction directory so any relevant change invalidates the procedure. The
procedure must preserve mandatory and forbidden source rules, contain no
credentials or request-specific artifacts, replace the current
workspace root with a runtime value, and record the exact
verified interfaces and required output template—not an idealized or
reconstructed summary.

Never assign a request-bound variable to a literal in a cached action block.
The executing agent binds it from the current request before using the block.

Remember it directly from standard input as the final tool call:

```bash
"{{PLAN_EXEC_CACHE}}" remember --key <domain.operation.variant> \
  --source <instruction-file-or-directory> \
  --procedure-file - <<'PROCEDURE'
<Markdown procedure>
PROCEDURE
```

Do not answer a successful miss before `remember` succeeds. On a hit, do not
rewrite an unchanged procedure. If a hit becomes stale, complete and verify the
repair, then replace it using the same key.
