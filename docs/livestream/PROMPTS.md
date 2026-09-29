# Two-stage prompt flow

These prompts exercise a separately provisioned QA deployment after its services and assets are ready. They are not commands to reconstruct a preloaded presenter machine. Use newly registered source identities from your own inventory.

## Stage 1: Search and native realtime VLM Alerts

Upload and index the original factory video, verify retention, and leave Filling hidden. Start with plain chat and no manual **+ Chat** selection:

> Show me liquid leaking from bottles

Inspect the current Search receipt and result cards. Play the returned 65–70-second candidate and seek. Then, in the same conversation:

> Find liquid leaking from bottles

Require another current Search job. A prose-only response that reuses earlier history is a failure. Follow-ups such as “Show me that clip” should remain usable without being falsely classified as a new Search.

For Alerts, first create the original-factory RTSP replay and confirm advancing frames. A suitable request is:

> Add a realtime alert on the factory replay for visible liquid physically escaping a bottle or the filling area. Use that live source, and show the rule and resulting evidence.

Resolve “factory replay” to the actual source in the QA deployment. Verify rule creation, live evaluation and alert evidence independently. Test replay end-of-stream/restart explicitly: automatic stale-decoder recovery is a known open defect.

## Stage 2: Supplied Filling extension

Enable the supplied extension using its QA recipe, reveal Filling, and use the distinct Sammy v3 live replay with matching calibration. This activates existing code and supplied models; it is not a claim of generating a new feature or training a model during the demo.

After starting a QA live session, exercise requests such as:

> Show the current Filling status and visible liquid-height measurements.

> Show the underfilled bottles observed in this live session.

> Show the overflow events and their available evidence from this session.

Require answers bound to actual current session observations. Describe visible height rather than volume. Disclose the separate calibrated exterior-color overflow signal. Do not replace live Filling with archive Search, a recorded result cache, or scripted expected anomaly counts.

Record the query wording and actual tools/results in the [test plan](TEST-PLAN.md). Specific successful prompts do not establish unrestricted natural-language coverage.
