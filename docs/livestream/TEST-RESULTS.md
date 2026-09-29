# QA source validation — 2026-09-29

**972 tests passed; 5 tests were skipped because they require external video-analysis assets.** These checks ran against the isolated `feat/livestream-filling-analysis` worktree. No fresh deployment or live model accuracy evaluation was performed. No existing runtime was modified.

| Check | Result | Scope |
|---|---:|---|
| CLI/core unit tests | 465 passed | Nine changed/new suites; 9.52 seconds |
| RT-VLM overlap patch checks | 4 passed | AST/compile checks against patch applied to source |
| Compose render/schema | Passed | Same 28 service names as the current deployment; no containers started |
| Python syntax compilation | 70 files passed | All changed/new Python files at the initial validation snapshot |
| UI app | 149 passed | 8 suites |
| UI common | 11 passed | 1 suites |
| UI chat | 41 passed | 2 suites |
| UI search | 62 passed | 5 suites |
| UI video-management | 36 passed | 3 suites |
| Filling UI TypeScript | Passed | `tsc --noEmit --project tsconfig.filling.json` |
| Extension backend/streaming CPU tests | 193 passed, 5 skipped | Run after two repository-relative fixture path fixes; 7.78 seconds |
| Segmentation contract tests | 11 passed | Offline run; 0.43 seconds |

The five extension skips require independently computed video analysis through `VISION_ANALYSIS_PATH`. They are not counted as passes. The additional run covered `segmentation/tests` and `segmentation/bottle/test_contract.py` using the same offline cached CPU test image and isolated-source bind. No GPU inference was started.

## Source provenance

The cached Python 3.13 interpreter supplied installed dependencies. `PYTHONPATH` pointed first to this worktree. Import locations were verified to be `libs/vss/cli/src/vss_cli/__init__.py` and `libs/vss/core/src/vss_core/__init__.py` inside this isolated checkout.

UI source was copied from the isolated worktree into an ephemeral `vss-ui-filling-builder:20260928-search-repair-v3` container. All 382 copied source files were checked byte-for-byte by SHA-256 before testing. Cached `node_modules` supplied dependencies. The container used `--network=none`, requested no GPU access and published no ports; it was removed after testing. Tests used the copied branch source, not the original host checkout.

## Focused commands

From the isolated worktree, with its CLI/core directories first on `PYTHONPATH`. Set `VSS_QA_PYTHON` to a Python 3.13 environment with this workspace's test dependencies installed; the validation run reused a cached interpreter.

```sh
PYTHONPATH="$PWD/libs/vss/cli/src:$PWD/libs/vss/core/src" \
  "$VSS_QA_PYTHON" -m pytest \
  -c libs/vss/pyproject.toml -q -o addopts= \
  libs/vss/cli/tests/unit_test/cli/test_job_grammar.py \
  libs/vss/cli/tests/unit_test/cli/test_filling_group.py \
  libs/vss/cli/tests/unit_test/cli/test_filling_live.py \
  libs/vss/core/tests/unit_test/lib/critic/test_critic.py \
  libs/vss/core/tests/unit_test/lib/search_core/test_embed_helpers.py \
  libs/vss/core/tests/unit_test/lib/search_core/test_embed_search.py \
  libs/vss/core/tests/unit_test/lib/search_core/test_search_archive_e2e.py \
  libs/vss/core/tests/unit_test/lib/search_core/test_vlm_openai.py \
  libs/vss/core/tests/unit_test/lib/vios/test_media_plane.py
```

The actual driver also asserted import provenance and wrote JUnit output. These were the UI commands, after the source copy:

### app

```sh
cd /repo/services/ui/apps/nv-metropolis-bp-vss-ui
/usr/local/bin/node /repo/services/ui/node_modules/jest/bin/jest.js --runInBand --runTestsByPath __tests__/utils/server/agentAdapter.test.ts __tests__/utils/server/agentAdapterConnectors.test.ts __tests__/utils/server/archiveSearch.test.ts __tests__/utils/server/fillingOperator.test.ts __tests__/hooks/useChatSidebarMainTabBridge.test.ts __tests__/utils/sidebarMainTabChatSubscribers.test.ts __tests__/utils/tabChatEnv.test.ts __tests__/components/Home.test.tsx --json --outputFile=/qa-results/ui-app.json
```

### common

```sh
cd /repo/services/ui/packages/common
/usr/local/bin/node /repo/services/ui/node_modules/jest/bin/jest.js --runInBand --runTestsByPath __tests__/utils/videoUpload.test.ts --json --outputFile=/qa-results/ui-common.json
```

### chat

```sh
cd /repo/services/ui/packages/nv-metropolis-bp-vss-ui/chat
/usr/local/bin/node /repo/services/ui/node_modules/jest/bin/jest.js --runInBand --runTestsByPath __tests__/ChatPanel.test.tsx __tests__/agentApi.test.ts --json --outputFile=/qa-results/ui-chat.json
```

### search

```sh
cd /repo/services/ui/packages/nv-metropolis-bp-vss-ui/search
/usr/local/bin/node /repo/services/ui/node_modules/jest/bin/jest.js --runInBand --runTestsByPath __tests__/components/SearchComponent.source-scope.test.tsx __tests__/components/SearchHeader.test.tsx __tests__/components/FilterPopover.test.tsx __tests__/utils/searchFilters.test.ts __tests__/utils/agentResponseParser.test.ts --json --outputFile=/qa-results/ui-search.json
```

### video-management

```sh
cd /repo/services/ui/packages/nv-metropolis-bp-vss-ui/video-management
/usr/local/bin/node /repo/services/ui/node_modules/jest/bin/jest.js --runInBand --runTestsByPath __tests__/videoDelete.test.ts __tests__/components/StreamCard.test.tsx __tests__/chunkedUpload.test.ts --json --outputFile=/qa-results/ui-video-management.json
```

### filling-typecheck

```sh
cd /repo/services/ui/apps/nv-metropolis-bp-vss-ui
/usr/local/bin/node /repo/services/ui/node_modules/typescript/bin/tsc --noEmit --project tsconfig.filling.json
```

## Bounded source review

The reviewed live cycle path derives events from arriving RF-DETR masks and persistent exterior-color observations. It does not use a prerecorded anomaly timeline. Profile code binds calibration to explicit media hashes and camera geometry; this constrains supported inputs rather than proving generalization to arbitrary videos.

Machine-specific training paths and older operating notes found during review were replaced with configurable paths and portable documentation. A deployed source UUID appears in a unit-test fixture, not in runtime source selection in the paths inspected. No fabricated result or forced confirmed verdict was found in that bounded review.

## Receipts and limits

Local-only receipts are under `/tmp/vss-qa-validation-20260929`: `python-provenance-compile.json`, `python-run.json`, `python-tests.xml`, `ui-source-provenance.json`, `ui-summary.json`, and per-package Jest JSON/logs. Unit-test fixtures are not live inference evidence.

No fresh-checkout deployment, restored-model run, browser/operator rehearsal, or eight-anomaly accuracy score was produced by these source checks. Follow TEST-PLAN.md for those acceptance stages.

The final packaged backend/streaming/segmentation source was rechecked together: 204 passed and 5 asset-dependent skips in 7.64 seconds. This repeated verification is included in the total above, not counted twice.
