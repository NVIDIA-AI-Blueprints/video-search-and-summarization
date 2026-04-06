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

- **Apache-2.0** — applies to all code in the repository except the `ui/` directory.
- **MIT** — applies to the original code under the `ui/` directory, which is derived from [NVIDIA NeMo Agent Toolkit UI](https://github.com/NVIDIA/NeMo-Agent-Toolkit-UI/).

**All contributions to this repository, regardless of which directory they target, are accepted under the Apache-2.0 license.** Even if you are contributing changes to the `ui/` directory, your contribution will be licensed under Apache-2.0. The original `ui/` code retains its MIT license, but any additions or modifications contributed through this repository are Apache-2.0.

See the [LICENSE](LICENSE) file for the full license texts.

### Developer Certificate of Origin (DCO)

All contributions must include a DCO sign-off. By adding a `Signed-off-by` line to your commit messages, you certify that you wrote (or otherwise have the right to submit) the contribution, and that you are licensing it under the Apache-2.0 license.

To sign off, add the `-s` flag when committing:

```bash
git commit -s -m "Your commit message"
```

This appends a line like:

```
Signed-off-by: Your Name <your.email@example.com>
```

If you have already made commits without a sign-off, you can amend the most recent one:

```bash
git commit --amend -s --no-edit
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
