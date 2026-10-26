# Experimental person-state routing evidence

This optional benchmark profile corrects lost person-search intent in bare
apparel queries and prevents ordinary person states or camera viewpoints from
being mistaken for external scene requirements. It sends scene/motion searches
the original query and person searches a fixed photo prefix plus the original.
Generated captions and inferred source/time metadata never enter retrieval.
No query IDs, cohort labels, GT, saved routes, critic or learned reranker enter
live selection. The agent and CLI defaults are unchanged.

## Controlled regression cohort

The existing v1.1.2 diagnostic gallery has 80 clips, 350 event and 250 PAS
queries. Retrieval depth is 80; full AP divides by all positive clips, and
AP@10 divides by min(positive clips, 10). Missing person sources remain misses.
The automatic person index contains 14,294 observations over 72 sources; the
Cosmos gallery covers all 80. Models, indices, GT, timestamp scope and SDK
execution are held fixed. Nemotron-3.5-Lightning-30B-A3B routes at temperature
zero, thinking off, maximum completion 32768 tokens. This is a repeatedly
examined regression cohort, not an unseen-data guarantee.

| Native live protocol, 600 queries | Full mAP | mAP@10 |
|---|---:|---:|
| Review baseline, original text and independent-list RRF | 68.1461% | 66.3379% |
| Fresh person-state run with bare-apparel clarification | 71.7570% | 70.1909% |
| Plus action-inflection / camera-phrase normalization, paired | 72.0583% | 70.5034% |

The fresh run made 600 unique routing calls and 600 SDK searches; six CLI
controls returned identical full result data. Mean routing+retrieval was
376.67 ms, median 284.39 ms, p95 586.61 ms, campaign wall 234.03 s. These are
persistent SDK timings including live routing; companion CLI startup is
excluded. The normalization change shared the same fresh model responses,
executed both parent and candidate requests for all five changed routes in
balanced order, and verified six unchanged controls. Every parent/control
result matched exactly. No new latency claim is made for that paired stage.

The final paired 539-query frame-supported view is 73.8232% full mAP and
71.9891% mAP@10. It uses 189 PAS queries and their frame-supported positives;
it does not replace the released600 target. Neither native result reaches75%.

## Action-only source guard

A later matched run made 600 fresh routing calls with the same model settings,
original queries, gallery and deployed assets. The person-state model sometimes
selected the person-image expert for action-only requests such as `a person
walking`. The profile now uses embedding when the entire original query fits a
conservative action grammar. Unknown appearance words, numbers, negation and
non-English descriptions retain the previous expert decision; an absent word
in an appearance dictionary does not prove the absence of an appearance filter.

| Native SDK results, same fresh responses | Full mAP | mAP@10 |
|---|---:|---:|
| Previous person-state selection | 71.9472% | 70.3923% |
| With action-only source guard | 72.2322% | 70.6714% |

The guard changed ten routes from attribute to embedding. Event scores were
unchanged; PAS full mAP increased from 58.4664% to 59.1504%. Both versions use
actual SDK outputs from the same matched run. The integrated runner's selected
path, original text, request parameters and results were checked for all 600
queries. The comparison retains native person coverage, including missing
sources; it needs no reference gallery, precomputed prototype bank or captured
index vectors at runtime. The extra SDK requests in the shared study supported
separate experimental controls, rather than a new requirement of this profile.

The same study also tested source constraints and embedding-based coverage
completion. Those separate, unintegrated experiments scored 72.8961% full mAP
and 70.8721% mAP@10 with the guarded routing decision. These are not the native
profile results above. Neither protocol reaches 80%, and neither establishes
unseen-dataset generalization.

The external artifact bundle `vss-native-map80-20261025` contains
`live-appearance-gate600/LIVE-REPORT.json` and
`integrated-action-gate600/RESULTS.json`. The latter records exact parity
between the integrated runner and the actual fresh SDK requests. Original
frozen and revised source-guard records remain available in the bundle.

## Reference-gallery distinction

The supplied PAS evaluation uses 285 release-selected representative person
crops covering all80 clips. With the existing SigLIP2 ONNX, original cased text
and max track similarity per clip, independent inference reproduced66.4194%
full mAP and60.6476% AP@10 on the same189 valid PAS queries, versus66.41/60.63
reported. Hit@1/5/10 match the published63.49/91.53/95.77%. Source PTH/H5
identity and the small numerical residual remain unverified.

Substituting those release-selected crops into the fresh routing captures
projects75.1856% full mAP /72.8605% AP@10 on600. That is an **offline gallery
intervention**, not native automatic-ingestion or fresh live proof of75%.
The native and reference protocols must be reported separately.

## Reproduction

Use the generic configured-deployment command in
[raw-sdk-runner.md](raw-sdk-runner.md#experimental-person-state-selection),
with sufficient depth for the complete gallery and the exact dataset version,
models and ingestion recipe under comparison. Do not reuse saved routing as
live input. Captures support offline rescoring under each declared GT view.

Large experiment captures are deliberately outside Git. This session's
artifact bundle is `vss-live-map75-20261025`:
`BARE-PERSON-RESULTS.json`, `bare-person600/`,
`state-normalization-pair/RESULTS.json`, `REPORT.md`, and
`reference-representatives/RESULTS.json` preserve the execution and evidence.
The artifact bundle is external evidence, not a runtime dependency.
