---
name: configure-webhook-bdd-test
description: Configure the Docker Compose webhook receivers required by the VIOS webhook notification BDD tests, including the custom body template cases. Use when preparing deployment/stream-processing/docker-compose/configs/notification_config.json for test_webhook_notifications.py or test_webhook_custom_body.py, or when those tests time out waiting for camera_add, camera_streaming, camera_remove, or a custom body delivery.
---

# Configure Webhook BDD Test

Every BDD webhook receiver is declared in one file:
`test/bdd_tests/data/webhook_bdd_config.json`. It is the single source of
truth shared by three consumers:

- `test/bdd_tests/scripts/update_notification_config.py` applies it to the
  deployed `notification_config.json`.
- `tests/notification/test_webhook_custom_body.py` reads `custom_body_cases`
  to know each case's path, method, and template.
- The `standard_receivers` paths must match `webhook_paths` in
  `test/bdd_tests/config.json` (the script warns on divergence).

**Never hand-edit the BDD receivers in `notification_config.json`.** Change
`webhook_bdd_config.json` (or accept it as-is) and run the script:

```bash
cd test/bdd_tests && python3 scripts/update_notification_config.py
```

By default it updates
`deployment/stream-processing/docker-compose/configs/notification_config.json`;
pass `--notification-config <path>` for another deployment, or `--dry-run` to
preview. The script sets `webhooks.enabled: true`, owns the three items
`wh-001`/`wh-002`/`wh-003` (`camera_add`/`camera_streaming`/`camera_remove` —
the ids are asserted as `webhook_id` by the tests), rewrites BDD requests in
place matched by URL, prunes stale `/bdd/webhooks/` receivers, and preserves
every non-BDD item and receiver. It refuses to run if the existing config is
not valid JSON — restore a valid file first.

## What the declared receivers cover

- **File-sensor receivers** (`camera_type: ["file"]`) at
  `/bdd/webhooks/camera/{add,streaming,remove}` — used by the file upload
  scenarios in `webhook_notifications.feature`.
- **RTSP receivers** (`camera_type: ["rtsp"]`) at the `/rtsp-only` paths —
  used by the RTSP scenario (skipped unless
  `tests.notification_tests.test_parameters.rtsp_sensor` is set in
  `test/bdd_tests/config.json`), and as the negative filter case:
  `camera_add/rtsp-only` must receive *nothing* for a file sensor. Keep it
  filtered to `["rtsp"]` exactly.
- **Unfiltered receiver** at `/bdd/webhooks/camera/add/unfiltered` — proves an
  omitted `camera_type` accepts every type.
- **Custom body cases** (`custom_body_cases`) at `/bdd/webhooks/custom/...` —
  used by `webhook_custom_body.feature`:
  - `valid`: scalar/object/array placeholders, missing paths rendering as
    `""`, preserved literal types, an empty `{}` body, the 32-level depth
    boundary, the default-shaped `camera_streaming` body whose metadata
    placeholders resolve, `body` winning over a configured
    `user_defined_metadata`, the body-less `user_defined_metadata`
    passthrough (merged verbatim into `event.metadata` of the default tagged
    body), and the Elasticsearch delete-by-query body on `camera_remove` with
    receiver-specific `query_params` (a case may override the shared
    `request_defaults` keys). Each delivered body must equal the body computed
    by the reference implementations in `webhook_test_utils.py` from the
    default receiver's capture of the same event.
  - `invalid`: malformed/embedded/empty placeholders, braces in property
    names, bare reserved braces, and a 33-level body. These requests must be
    present in the config so the tests can prove VST skips them at load while
    the valid sibling receivers still deliver.

When adding a case, give it a unique `case` name and a unique path under
`/bdd/webhooks/custom/` (invalid cases under `/bdd/webhooks/custom/invalid/`),
then rerun the script. Test code and data are bind-mounted into the BDD
container, so no container rebuild is needed.

All BDD requests share `request_defaults`: the `streamId` header (the tests
read it case-insensitively but do not accept `x-stream-id`), the `change`
query parameter, `timeout_ms`, and `retry`. Omit `auth`; the in-process
receiver does not verify signatures and an unresolved `{{secrets.*}}`
placeholder only adds a failure path (the script strips item-level `auth`
from the BDD items).

## After applying the config

1. Confirm the deployed image actually supports webhooks. Images built before
   the webhook feature ignore this config entirely, and the only symptom is
   every scenario timing out with zero captured requests:

```bash
docker exec streamprocessing-ms-1 grep -ac "notification_config.json" /home/vst/vst_release/launch_vst
```

`0` means the running image predates the feature regardless of its tag —
rebuild (`./build.sh container module=sensor,streamprocessing tag=<tag>`) and
redeploy before running the tests. `1` or more means webhook support is
present. Custom body templates additionally require an image built from a
branch containing `renderBodyTemplate` in
`src/framework/notification/webhook/webhook_notifier.cpp`.

2. Tell the user to restart VST if it was already running; VST loads this
   config at startup, and the invalid-template skip happens at that load.
3. Do not run pytest unless requested. Finish by giving the commands, to be
   run from `test/bdd_tests`:

```bash
poetry run pytest tests/notification/
poetry run pytest tests/notification/test_webhook_notifications.py -v --tb=short --log-cli-level=INFO
poetry run pytest tests/notification/test_webhook_custom_body.py -v --tb=short --log-cli-level=INFO
```
