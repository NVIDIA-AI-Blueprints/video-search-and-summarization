# Demo QA test plan

Run on an isolated deployment created from this checkout using the [QA recipe](../../extensions/filling-analysis/deployment/qa/README.md). Record commit, image digests, architecture, GPU/model placement, asset hashes, browser version and service readiness. Keep credentials and private addresses out of committed reports. Record measured outcomes; a test below is not evidence that it passed.

Use [TEST-RESULTS.md](TEST-RESULTS.md) for the checks performed on this branch. Browser and GPU integration tests are separate from unit tests.

## 1. Build and readiness

- Verify the external asset hashes and build the UI and extension images from this checkout. Resolve the baseline composition and run the recipe's configuration checks before launch.
- Confirm archive ingest, embeddings, Search, critic/VLM, VIOS, realtime VLM Alerts, UI and native agent endpoints are ready. Capture failures rather than treating HTTP reachability alone as functional readiness.
- Configure the checkout CLI with `uv run --project libs/vss vss`; use its help/operations workflow for the new deployment. Verify the native harness runs the intended checkout/build and can execute tools.
- Run focused CLI/core, UI adapter/filter/upload, and Filling tests. Use the commands and results recorded in [TEST-RESULTS.md](TEST-RESULTS.md); do not infer GPU or browser acceptance from mocked tests.

## 2. Archive Search and retention

Upload the original factory video, confirm protection/retention and indexed chunks, then use plain chat with no selected source:

> Show me liquid leaking from bottles

Require a distinct current successful Search tool/job receipt, correctly bound result cards, source identity and clip offsets. Visually inspect the 65–70-second candidate, play it, and seek within it. Record the critic verdict separately from what the footage shows.

Repeat in the same conversation:

> Find liquid leaking from bottles

Require a new Search receipt; old history, inventory prose and cached cards do not satisfy a fresh Search. Check that the requested visual query reaches Search without being reduced to a generic “sweet spot.” Record latency and critic false positives/negatives.

Regression cases:

| Case | Expected result |
| --- | --- |
| Persisted RTSP sidebar selection before the archive query | New archive cards are visible with the applicable source scope; no silent stale RTSP filter hides them. |
| User changes a filter after cards arrive | The selection persists until a new answer legitimately reconciles scope; no render loop or repeated override. |
| Fresh Search returns prose but no current successful Search receipt | Explicit failure; no invented completion or historical artifacts masquerading as fresh results. |
| “Show me that clip” or a specific result/offset follow-up | Ordinary follow-up behavior; not incorrectly blocked as a new archive search. |
| Inventory/history question or Filling request | Not forced into the archive Search guard. Mixed requests require review of their actual tool behavior. |
| Inventory includes a source explicitly marked removed | Listing and archive resolution skip its tombstone without requesting its streams. |
| An active source's stream request returns an error | Error remains visible; not silently converted to an empty inventory. |
| New UI upload | Verify retention protection on the registered recording; existing recordings need their own check. |

For an all-indexed-archive claim, test multiple distinct indexed recordings and inspect the actual Search scope. A successful unscoped CLI run or one model-selected recording alone does not prove broad agent coverage.

## 3. Realtime VLM Alerts

Keep Filling hidden for this stage. Use the separate original-factory RTSP replay and a native realtime VLM rule for visible liquid physically escaping a bottle or filling area. Verify the rule's source binding, advancing decoder timestamps, incoming VLM evaluations, alert delivery and playable evidence.

Check normal footage as a negative control, then a visibly leaking interval. Keep visual ground truth separate from the model's wording. Record actual source/rule identities privately with the run evidence.

Exercise replay end-of-stream and restart while the rule remains configured. Verify the decoder actually resumes fresh frames and alert evaluation. **Known open defect:** the demonstrated replay left a stale decoder after end-of-stream; manual repair was required on September 29. No automatic recovery fix is included. If repair is needed, record the failure, repair actions and recovery separately; do not mark uninterrupted recovery as passed.

## 4. Supplied Filling extension

Enable the built extension and reveal Filling using the QA recipe. Reload the browser if required. Use Sammy v3's separate live source with its matching allowlist/calibration and begin a new QA session.

- Verify real advancing frames, RF-DETR bottle/liquid masks and same-frame overlay provenance. Check the actual GPU assignment; the original worker used GPU 2, not a required universal device number.
- Verify visible-height values, underfill logic and completed-cycle counting on received frames. Missing masks are unknown; interrupted tracks are incomplete, not completed bottles.
- Verify overflow uses the separately disclosed calibrated exterior-color signal and stationary-bottle gate. It must not substitute expected event schedules or treat model fill level alone as a spill.
- Check queued-frame dropping, source interruption, reconnect behavior, event deduplication and persistence. A reconnect must not join evidence across decoder epochs.
- Ask for status, underfills and overflow evidence. Counts must come from the current live session. Repeated loops are not independent accuracy trials.
- Verify snapshots and any offered clip links. Receiver-estimated timestamps are not verified camera-capture timestamps; with unverified recording alignment, video evidence must remain pending rather than fabricated.

Hide/reveal Filling and verify existing backend state is preserved where the deployment supports UI-only visibility changes. Avoid equating navigation visibility with service readiness.

## Report criteria

Report Search tool execution, card rendering, playback, retrieval accuracy, alert delivery/recovery and Filling measurement/provenance as separate outcomes. Include exact failures and reproduction steps. The limited historical Search checks do not close full fresh-host, long-run replay, arbitrary-camera, or Spark validation.
