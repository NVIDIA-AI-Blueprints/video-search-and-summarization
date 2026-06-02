# Contributing to Video Search and Summarization

If you are interested in contributing to Video Search and Summarization (VSS), your contributions will fall into the following categories:

1. You want to report a bug, feature request, or documentation issue
    - File an [issue](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/issues/new/choose)
    describing what you encountered or what you want to see changed.
    - The team will evaluate the issues and triage them, scheduling
    them for a release. If you believe the issue needs priority attention,
    comment on the issue to notify the team.
2. You want to propose a new feature and implement it
    - Post about your intended feature, and we shall discuss the design and
    implementation.
    - Once we agree that the plan looks good, go ahead and implement it, using
    the [code contributions](#code-contributions) guide below.
3. You want to implement a feature or bug-fix for an outstanding issue
    - Follow the [code contributions](#code-contributions) guide below.
    - If you need more context on a particular issue, please ask and we shall
    provide.

## Licensing

This project uses a dual-license model:

- **Apache-2.0** — applies to all code in the repository except the `services/ui/` directory.
- **MIT** — applies to the original code under the `services/ui/` directory, which is derived from [NVIDIA NeMo Agent Toolkit UI](https://github.com/NVIDIA/NeMo-Agent-Toolkit-UI/).

**All contributions to this repository, regardless of which directory they target, are accepted under the Apache-2.0 license.** Even if you are contributing changes to the `services/ui/` directory, your contribution will be licensed under Apache-2.0. The original `services/ui/` code retains its MIT license, but any additions or modifications contributed through this repository are Apache-2.0.

See the [LICENSE](LICENSE) file for the full license texts.

## File-level license headers

If your contribution adds or modifies a source file, please:

- Add or update the SPDX file header so that it reflects the correct copyright
  holders and license identifier.
- For new files authored by you (or your employer) on behalf of this project,
  use the SPDX identifier `Apache-2.0` and add the appropriate copyright
  notice.
- For changes to files derived from third-party sources:
  - Preserve the existing third-party copyright and license notices. 
  - List the file(s) you modified in your change description.
  - Keep the existing SPDX identifier as a file's SPDX identifier should follow its upstream license.
  - Each component records its derived files and their upstreams in its own `NOTICE` and `3rdParty_Licenses.md`. If you need to vendor new third-party code, note it in your change description so that the appropriate notice can be added to `NOTICE` and `3rdParty_Licenses.md`.
  - For example, in Spatial AI Data Utils:
    - nuScenes dev-kit-derived files and TrackEval-derived files are listed in [`NOTICE`](libs/analytics/spatialai-data-utils/NOTICE).
    - nuScenes dev-kit-derived files (upstream Apache-2.0, Copyright 2021 Motional) stay `Apache-2.0`.
    - TrackEval-derived files (upstream MIT, Copyright (c) 2020 Jonathon Luiten) are dual-licensed `MIT AND Apache-2.0`.
- Do not introduce code under licenses that are incompatible with Apache-2.0 distribution.

### Signing Your Work — Developer Certificate of Origin (DCO)

We require that all contributors "sign off" on their commits. This certifies that the contribution is your original work, or that you have rights to submit it under the same license, or a compatible license. Any contribution which contains commits that are not Signed-Off will not be accepted.

To sign off on a commit you simply use the `--signoff` (or `-s`) option when committing your changes:

```bash
git commit -s -m "Add cool feature."
```

This will append the following to your commit message:

```
Signed-off-by: Your Name <your@email.com>
```

If you have already made commits without a sign-off, you can amend the most recent one:

```bash
git commit --amend -s --no-edit
```

By adding a `Signed-off-by` line you are certifying that the contribution complies with the Developer Certificate of Origin (DCO) reproduced below.

#### Full text of the DCO

Source: <https://developercertificate.org/>

```
Developer Certificate of Origin
Version 1.1

Copyright (C) 2004, 2006 The Linux Foundation and its contributors.

Everyone is permitted to copy and distribute verbatim copies of this
license document, but changing it is not allowed.


Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```

**Pull requests with unsigned commits will not be merged.**

## Code contributions

### Your first issue

1. Read the project's [README.md](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/blob/main/README.md)
    to learn how to set up the development environment.
2. Find an issue to work on. The best way is to look for the [good first issue](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
    or [help wanted](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22) labels.
3. Comment on the issue saying you are going to work on it.
4. Code! Make sure to update unit tests!
5. When done, [create your pull request](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/compare).
6. Verify that CI passes all [status checks](https://help.github.com/articles/about-status-checks/), or fix if needed.
7. Wait for other developers to review your code and update code as needed.
8. Once reviewed and approved, a maintainer will merge your pull request.

Remember, if you are unsure about anything, don't hesitate to comment on issues and ask for clarifications!

### Pull request guidelines

- Provide a clear description of the changes in your PR.
- Reference any issues closed by the PR with "closes #1234".
- Ensure new or existing tests cover your changes.
- Keep the documentation up to date with your changes.

### Local development and testing

Before opening a PR, please enable the pre-commit hooks and, ideally, run the GitHub Actions CI locally. Both workflows mirror the checks that run on CI, so passing them locally means the PR will pass remote checks.

#### 1. Enable pre-commit hooks

The repository ships with a `.pre-commit-config.yaml` at the root that runs the same lint and type-check steps as CI:

- `ruff check` — mirrors the CI `Lint (Python)` job
- `ruff format --check` — mirrors the CI `Lint (Python)` job
- `mypy src/vss_agents/` — mirrors the CI `Type Check (mypy)` job
- TruffleHog secret scan

Install the hooks once after cloning the repo:

```bash
cd services/agent
uv venv --python 3.13
uv sync --group dev
uv run pre-commit install
```

After that, the hooks run automatically on every `git commit`. To run them manually against all files:

```bash
uv run pre-commit run --all-files
```

If you need to bypass a hook for a specific commit (rare, and not recommended), use `SKIP=<hook-id> git commit …` or `git commit --no-verify`.

#### 2. Run the CI workflow locally with `act`

The full GitHub Actions CI pipeline (`.github/workflows/ci.yml`) can be executed locally using [`act`](https://github.com/nektos/act), which runs the workflow in Docker containers that match GitHub's runners.

Install `act` (see its [README](https://github.com/nektos/act#installation) for other platforms):

```bash
# Linux / macOS (Homebrew)
brew install act

# Linux (curl)
curl https://raw.githubusercontent.com/nektos/act/master/install.sh | sudo bash
```

You'll also need Docker running locally.

From the repository root, run the push workflow:

```bash
act push --workflows .github/workflows/ci.yml
```

This executes every job in parallel (lint, typecheck, test, security scan, UI lint/typecheck, UI build). The first run downloads the `catthehacker/ubuntu:act-latest` image (~1 GB); subsequent runs reuse the cached image.

Notes:

- `act` does not have GitHub's `ACTIONS_RUNTIME_TOKEN`, so the `actions/upload-artifact` step in the `Test (pytest)` job will fail at the end. This is expected and does not affect the test result — the tests themselves will have already passed.
- To run a single job, pass `-j <job-id>`, e.g. `act push -j lint`.
- If you hit GitHub API rate limits on `actions/setup-node`, pass a token: `act push --secret GITHUB_TOKEN=$(gh auth token)`.

### Branch naming

Branches used to create PRs should have a name of the form `<type>/<name>` which conforms to the following conventions:

- Type:
    - `feat` - For new features
    - `fix` - For bug fixes
    - `docs` - For documentation changes
    - `refactor` - For code refactoring
    - `test` - For adding or updating tests
- Name:
    - A name to convey what is being worked on
    - Please use dashes between words as opposed to spaces.

## Attribution

Portions adopted from the [NVIDIA PLC-OSS-Template](https://github.com/NVIDIA-GitHub-Management/PLC-OSS-Template).
