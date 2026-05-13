# Skills NV-BASE CI

Runs the NV-BASE Tier 1 `skills-check` against `skills/` on every PR
that touches the skill tree or this harness, and fails the check if
any HIGH finding remains after allow-listing.

## Files

| File | Role |
|---|---|
| [`../workflows/skills-nv-base.yml`](../workflows/skills-nv-base.yml) | GitHub Actions workflow definition |
| [`run_check.py`](run_check.py) | Stdlib-only driver that locates nv-base, runs skills-check, parses output, emits annotations |
| `README.md` | this file |

## Where it runs

Self-hosted runner labelled **`nv-base`**. NV-BASE is not publicly
distributed, so the workflow does not install it at run time — the
binary is pre-installed by the runner operator (see *Runner bootstrap*
below).

## Runner bootstrap (one-time, by operator)

1. Provision a host with network access to the internal NV-BASE
   distribution channel and to `api.github.com`.
2. Install nv-base into a dedicated venv:

   ```bash
   sudo python3 -m venv /opt/nvbase-venv
   sudo /opt/nvbase-venv/bin/pip install --upgrade nv-base
   /opt/nvbase-venv/bin/nv-base --version
   ```

   The pip command needs the internal NV-BASE index URL; check the
   NV-BASE distribution docs for the current location. Operators with
   access should pin a version (`nv-base==X.Y.Z`) for reproducibility.

3. Register the host as a GitHub Actions self-hosted runner on this
   repository with the **`nv-base`** label
   (Settings → Actions → Runners → New self-hosted runner).
4. Confirm the workflow can resolve the binary — `${{ env.NVBASE_BIN }}`
   in [`../workflows/skills-nv-base.yml`](../workflows/skills-nv-base.yml)
   defaults to `/opt/nvbase-venv/bin/nv-base`; adjust if your install
   path differs.

To refresh nv-base later, SSH to the runner and re-run the
`pip install --upgrade` line above. No workflow change is needed.

## What it gates on

`nv-base skills-check skills/` emits SCHEMA-HIGH, SCHEMA-MEDIUM, and
SCHEMA-LOW findings. The workflow gates **only on HIGH**, and even
there allow-lists IDs listed in the `NVBASE_ALLOW_HIGH` env var
(default: `author_missing`, which is template-omitted on purpose).

- Allow-listed HIGH → `::warning` annotation on the affected file, no failure
- Non-allow-listed HIGH → `::error` annotation, job exits 1

To tighten the gate, drop entries from the env var in
[`../workflows/skills-nv-base.yml`](../workflows/skills-nv-base.yml):

```yaml
env:
  NVBASE_ALLOW_HIGH: author_missing   # comma-separated NV-BASE check IDs
```

## Required status check

For the exit-1 to actually block merging, add
`Skills NV-BASE / skills-check` as a required status check on `develop`
(and `main`, once synced) under Settings → Branches / Rulesets.
Without that, a HIGH finding shows a red X but doesn't prevent merge.

## What's NOT in v1

- **No PR comment.** Findings surface as inline annotations only.
  Comment-poster pattern is straightforward to add later.
- **No `full-security-scan`, `validate`, or LLM-backed checks.**
  `skills-check` is the catalog-gate check most aligned with the
  publishing pipeline. The LLM-backed scans (`context-optimization-check`,
  `inter-skill-check`, `quality-check`) require additional model
  credentials on the runner and are a separate decision.
- **No agent-eval / Tier 3.** That's the existing skills-eval
  workflow's job; this one is Tier 1 static-and-schema only.
