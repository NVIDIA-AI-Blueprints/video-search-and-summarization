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
