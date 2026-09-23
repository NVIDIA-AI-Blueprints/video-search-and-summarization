# Delete a search source

Resolve exactly one source and save its UUID and canonical name before deletion.
Confirm the target unless deletion was already explicit. Deletion goes through
the CLI; the deployment's `camera_remove` webhooks withdraw the source from
RT-CV, RT-Embed, and RT-VLM, and run the upload-anchor Elasticsearch cleanups
(`mdx-raw`, `mdx-behavior`, `mdx-embed-filtered` on the `*-2025-01-01`
anchors). Never send a bare `DELETE` to VST, RTVI-CV, RTVI-Embed, storage-ms, or
Elasticsearch, and never issue an Agent `DELETE /api/v1/videos/<id>` — `vss
vios delete` is canonical:

```bash
: "${SAVED_SENSOR_ID:?save the exact file-source UUID before deletion}"
: "${SAVED_SOURCE_NAME:?save the canonical source name before deletion}"
resolve_upload_indexes || exit 1
: "${EMBED_INDEX:?resolve the exact upload embedding index first}"
: "${BEHAVIOR_INDEX:?resolve the exact upload behavior index first}"
: "${RAW_INDEX:?resolve the exact upload raw index first}"

DELETE_READINESS_DEADLINE=$(($(date +%s) + 600))
DELETE_TIMEOUT=$(source_timeout "${DELETE_READINESS_DEADLINE}" 60) || exit 1
vss vios delete --type video --sensor "${SAVED_SOURCE_NAME}" || exit 1

# Last-known state, so an expiry can say what is still present rather than
# only that it gave up.
VST_PRESENT=unknown EMBED_COUNT=unknown BEHAVIOR_COUNT=unknown RAW_COUNT=unknown
while :; do
  COUNT_TIMEOUT=$(source_timeout "${DELETE_READINESS_DEADLINE}" 15) || {
    # The delete was accepted; cleanup did not finish inside the deadline.
    # Report what is still there and exit 6 (partial) -- exiting 1 with a bare
    # message loses the half that did succeed, and reads as "delete failed"
    # when the source may already be gone and only an index still draining.
    printf 'delete_status=partial vst_present=%s counts=%s,%s,%s\n' \
      "${VST_PRESENT}" "${EMBED_COUNT}" "${BEHAVIOR_COUNT}" "${RAW_COUNT}" >&2
    echo "cleanup did not finish within the deadline; the values above are what is still present" >&2
    exit 6
  }
  VST_SENSORS=$(vss vios list) || exit 1
  VST_PRESENT=$(printf '%s' "${VST_SENSORS}" | jq -r \
    --arg id "${SAVED_SENSOR_ID}" --arg name "${SAVED_SOURCE_NAME}" \
    'any(.sensors[]; .sensor_id == $id or .name == $name)') || exit 1
  case "${VST_PRESENT}" in true|false) ;; *) exit 1 ;; esac
  EMBED_COUNT=$(index_count "${DELETE_READINESS_DEADLINE}" "${EMBED_INDEX}" sensor.id.keyword \
    "${SAVED_SENSOR_ID}") || exit 1
  BEHAVIOR_COUNT=$(index_count "${DELETE_READINESS_DEADLINE}" "${BEHAVIOR_INDEX}" sensor.id.keyword \
    "${SAVED_SOURCE_NAME}") || exit 1
  RAW_COUNT=$(index_count "${DELETE_READINESS_DEADLINE}" "${RAW_INDEX}" sensorId.keyword \
    "${SAVED_SOURCE_NAME}") || exit 1
  if [ "${VST_PRESENT}" = false ] &&
     (( EMBED_COUNT == 0 && BEHAVIOR_COUNT == 0 && RAW_COUNT == 0 )); then
    break
  fi
  sleep 10
done
printf 'delete_status=success vst_present=%s counts=%s,%s,%s\n' \
  "${VST_PRESENT}" "${EMBED_COUNT}" "${BEHAVIOR_COUNT}" "${RAW_COUNT}"
```

Reuse the same runtime values and poll until VST no longer lists the source,
the embedding tuple for the saved UUID is zero, and behavior/raw tuples for the
canonical name are zero. Report all counts. Never delete an ambiguous source or
issue independent backend cleanup. RTSP deletion uses
`vss vios delete --type stream --sensor <name>` and the same bounded absence
checks; never substitute a direct backend mutation.

## Known limitations

The shipped `camera_remove` cleanups target the `*-2025-01-01` anchor indices,
so **live-dated (RTSP) documents survive** deletion — a stream recorded across
midnight leaves today's shard behind. No shipped config cleans the VLM tag
index (`default_<streamId>`), so **tag-search hits survive** in full. Both are
documented limitations, not failures: report them, and do not improvise a
direct Elasticsearch `_delete_by_query` to paper over them.

For storage API version details use `vss-manage-video-io-storage`; use schemas
advertised by the exact running deployment rather than guessing. For Kubernetes,
do not query Elasticsearch directly; confirm absence through `vss vios list`
and a bounded retry of the search that found the source.
