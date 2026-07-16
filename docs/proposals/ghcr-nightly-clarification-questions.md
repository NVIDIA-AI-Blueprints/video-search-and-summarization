# GHCR continuous develop + GitLab nightly: decisions and clarifications

This is the trimmed decision document for PR #1190 and the companion
`ci-vss-oss` work. It contains:

1. decisions already clear enough to implement;
2. engineering details that do not require owner input; and
3. only the remaining policy or ownership questions.

Status markers:

- 🟢 **Resolved** — implementation may proceed.
- 🟠 **Needs owner clarification** — choose an option before activation.

---

## Resolved decisions

### 🟢 Release identity

- The authoritative whole-stack identity is the content-addressed
`release_set_id`.
- Image manifest digests are authoritative at image level.
- Human-readable tags are references to those digests, not the security
boundary.

### 🟢 Release-set composition

- Images in one release set may have different tags.
- Exact digests and source provenance must be recorded and checked.
- Existing source-tree verification remains required.

### 🟢 Nightly failure behavior

- A failed nightly does not update NGC staging.
- The previous validated/staged set remains available.
- Failed immutable images are retained for diagnosis.
- Merges into `develop` are not automatically blocked by one nightly failure.

### 🟢 Default deployment during migration

- Keep the current mixed per-image registry pins until every required image for
a supported dev profile is available and validated from GHCR.
- Do not flip the committed global default to GHCR merely because base works.

### 🟢 GHCR namespace and visibility

- Canonical namespace: `ghcr.io/nvidia-ai-blueprints/vss/<image>`.
- Every repo-native first-party image with a supported Dockerfile and published
tag should be available publicly from GHCR.
- Third-party, NIM, non-redistributable, and unapproved mirrored images are not
included automatically.

### 🟢 Initial retention

- Do not automatically delete GHCR versions during the initial rollout.
- Revisit retention and PR garbage collection after measuring package growth
and confirming safe tag-level deletion behavior.
- The currently proposed closed-PR GC workflow must therefore remain disabled
or be removed before activation.

### 🟢 Build ownership

- Repo-native managed images are built in GitHub.
- GitLab consumes the exact GHCR release set and does not rebuild those images.
- The existing GitLab-built image scope—agent, UI, and alert-ms—moves to
GitHub first.
- Broader source-ready components are follow-up migrations.

### 🟢 Architecture coverage

- Every GitHub-built managed image must publish both:
  - `linux/amd64`
  - `linux/arm64`
- GitHub verifies the registry manifest platform set, OCI source labels, and
digest.
- GitLab tests the produced images.

### 🟢 Build performance policy

- Reliability is required; there is no hard build-duration gate.
- Build duration should still be reported so regressions are visible.
- Use GitHub Actions BuildKit cache scoped per image.
- If architecture jobs are split later, cache scope must include architecture.

### 🟢 Source-absent images

- Do not mirror source-absent first-party images in the initial rollout.
- Keep those images on their existing explicit NGC pins.
- A dedicated internal mirror pipeline is deferred unless redistribution scope
changes later.

---

## Engineering conclusions — no owner question required

These are implementation choices that can be derived from the requirements,
code, or standard safety practices.

### Immutable references

- Retain immutable tags such as `develop-<sha>` and `pr-<N>-<sha>` alongside
any moving alias.
- Their purpose is discoverability and reproducibility after an alias moves.
- Promotion and validation still use `image@digest` from the release set.

### PR reporting

- A passing PR receives an idempotent marker comment/check summary containing:
  - release-set ID;
  - immutable image tags;
  - image digests; and
  - developer aliases when enabled.
- CI does not commit tag updates back to the PR branch because that changes the
SHA and creates a build loop.

### Downstream acceptance

- GitLab tests exact GHCR `image@digest` entries.
- A GHCR-managed image is never rebuilt during acceptance.
- Utility/evaluator images may still be built by GitLab.
- Images not yet migrated remain on their explicitly pinned source until they
are approved for build or mirror.

### Registry and tag override mechanics

- The initial GitHub-managed set (agent, UI, alert-ms) is selected with:
  - `VSS_CONTAINER_REGISTRY`
  - `VSS_CONTAINER_TAG`
- Per-image repository overrides remain available for development.
- Other first-party images retain explicit per-image pins until migrated.

### Multiarch implementation

- Do not build UI dependencies once on amd64 and copy them into arm64: the
dependency graph contains native SWC/Sharp/Turbo packages.
- Investigate the approved NVIDIA GitHub arm64 runner and use native
per-architecture fan-out when available.
- QEMU remains only a compatibility fallback.
- Attestation descriptors reported as `unknown/unknown` are not runnable
platforms and are excluded from platform-set comparison.

### Retry and failure safety

- Automatically retry bounded infrastructure failures only:
  - runner/system failure;
  - transient registry/network failure; and
  - capacity/timeout failure.
- Do not retry product, provenance, digest, license, or security failures
without new evidence or human action.
- A failure never mutates the validated channel.

### Credential boundaries

- GitHub repo-native builds use the scoped `GITHUB_TOKEN`.
- Fork code never receives `packages: write`.
- NGC credentials remain in GitLab/Vault.
- Public GHCR packages should be pulled anonymously where approved.
- Long-lived GitHub credentials are not passed inside release-set payloads.

### Promotion mechanics

- Copy GHCR to NGC dev without rebuilding.
- Preserve all architectures and verify digest parity.
- Use the immutable release-set tag, not `develop-latest`, as the promotion
input.
- Open artifacts-promotion MRs only after the configured blocking test matrix
passes.
- Roll back the complete release set, not individual images, to avoid untested
combinations.

---

## Owner decisions

### 🟢 Q1 — Which moving aliases exist, and when do they advance? **P0**

`origin/develop` identifies source code; an image alias identifies the prebuilt
containers a checkout pulls.

- A. `develop-latest` advances after a verified build; a separate
`develop-validated` or dated alias advances after nightly.
- B. `develop-latest` advances only after nightly validation.
- C. Publish only immutable/dated tags; no moving alias.
- D. Publish `develop-latest` but no validated alias.

Suggested default: **A**. It separates continuous developer convenience from
nightly validation.

Decision: **A**. `develop-latest` advances after a verified build.
`develop-validated` advances after nightly. Do not publish a `last-green` tag.

### 🟢 Q2 — Where is durable validated-release history stored? **P0**

The release set is authoritative, but a moving tag alone cannot show what was
previously validated or provide an atomic rollback history.

- A. Commit a bounded `last-green` lock/history in the GitHub repository.
- B. Store immutable release-set history in GitLab.
- C. Store it in an external release database/service.
- D. Keep no explicit history; rely on immutable registry tags and CI logs.

Suggested default: **A or B**. The location is less important than retaining an
auditable whole-set history.

Decision: **A**. Keep a bounded committed release-set lock/history for audit and
whole-set rollback, but do not expose it as a `last-green` registry tag.

### 🟢 Q3 — Which source-absent first-party images may be mirrored publicly? **P0**

- A. Mirror every first-party image required by supported dev profiles.
- B. Exclude every image whose source is not in the GitHub repository.
- C. Decide per image based on redistribution approval; retain an NGC override
when approval is absent.
- D. Mirror only alert verification as an initial exception.

Suggested default: **C**.

Decision: **B**. Do not mirror source-absent images in the initial rollout.
Keep their explicit NGC pins.

### 🟢 Q4 — What is blocking in the first production nightly? **P0**

- A. Base, LVS, search, alerts verification, alerts real-time, and warehouse
Compose jobs.
- B. The Compose matrix plus Helm/Kubernetes as a non-blocking signal.
- C. The Compose matrix plus blocking Helm/Kubernetes.
- D. Compose, Helm/Kubernetes, and x86/SBSA multi-hardware are all blocking
from day one.

Suggested default: **B**, then graduate Helm and multi-hardware after measuring
flake rate and capacity.

Decision: **B**. The Compose matrix blocks staging; Helm/Kubernetes runs
automatically as a non-blocking signal initially.

### 🟢 Q5 — What nightly schedule and overlap policy should be used? **P1**

- A. Daily at 02:00 UTC; skip a new run if the prior nightly is still active.
- B. Daily after a US-Pacific merge cutoff; queue one successor.
- C. Trigger after every green `develop` release set.
- D. Weekdays only at an infrastructure-selected window.

Suggested default: **A** initially. Never cancel a run after NGC mutation has
started.

Decision: **A**. Keep the current daily 02:00 UTC schedule. Skip a new run while
the prior nightly is still active; never cancel after NGC mutation starts.

### 🟢 Q6 — What is the canonical NGC dev namespace? **P0**

- A. `nv-metropolis-dev/met-moe-agents` for all managed VSS images.
- B. Preserve each component team’s current NGC namespace.
- C. Create a dedicated VSS namespace.
- D. Keep a per-image destination map permanently.

Suggested default: **D** during migration, then converge on **A or C** before
advertising one-registry switching for the complete stack.

Decision: **A** — `nv-metropolis-dev/met-moe-agents`.

### 🟢 Q7 — Which security findings block staging? **P0**

- A. New critical/high CVEs above an approved threshold plus OSRB/license
violations.
- B. Only exploitable critical findings.
- C. Any new CVE or license finding.
- D. Security results are advisory; a human always decides.

Suggested default: **A**, with an explicit waiver owner and expiry.

Decision: **D**. Security results are advisory; the human release owner decides.

### 🟢 Q8 — Who approves or merges artifacts-promotion MRs? **P0**

- A. Human release owner.
- B. SQA owner after validation/security pass.
- C. Auto-merge after all required gates pass.
- D. Component owner per image.

Suggested default: **A** initially; move to **C** only after rollback,
notification, and audit evidence are proven.

Decision: **A**. A human release owner approves and merges.

### 🟢 Q9 — What NVBug automation and ownership policy is required? **P1**

- A. File bugs for reproducible product, blocking CVE, OSRB/license, and
provenance failures; route through a canonical component-owner map.
- B. File security/OSRB bugs only.
- C. Produce a nightly report but do not file bugs automatically.
- D. Let each component team consume notifications and file its own bugs.

Suggested default: **C** for initial rollout, then **A** after deduplication and
owner routing are validated.

Decision: **C**. Produce a concise nightly report without automatic bug filing:
straight facts, evidence links, and suggested next action only.

### 🟢 Q10 — What must land before PR #1190 merges versus before activation? **P0**

- A. Merge #1190 once agent/UI/alert build, exact-digest downstream acceptance,
and safety contracts are complete; gate broader rollout behind feature flags.
- B. Block #1190 until all four dev profiles are GHCR-only.
- C. Block #1190 until Helm/multi-hardware, nSpect, and NVBug automation are
complete.
- D. Merge the SSOT/build foundation only; move all release-set and nightly
behavior into follow-up PRs.

Suggested default: **A**.

This decision should also confirm merge/activation order for:

1. GitLab compose compatibility MR;
2. GitLab nightly/acceptance MR;
3. GitHub PR #1190; and
4. GitHub PR #1181 canonical Compose migration.

Decision: **A**. PR #1190 may merge when agent/UI/alert GitHub builds,
exact-digest downstream acceptance, and safety contracts are complete. Broader
GHCR profile coverage, blocking Helm/multi-hardware, and later automation remain
activation-gated follow-ups.

---

## Exit condition for this questionnaire

Q1–Q10 are resolved. Remaining items are implementation or verification work
and should be tracked as tasks rather than additional MCQs.