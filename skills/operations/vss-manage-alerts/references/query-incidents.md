# Query Incidents (Workflow C — real-time incident store)

Operational reference for **Workflow C**: the full decision tree, guarded query blocks, and
response contract for `GET $AB/api/v1/realtime/incidents`. Use `$AB` from the parent skill
(Kubernetes `${VSS_PUBLIC_URL}/alert-bridge`, Docker `http://${HOST_IP}:9080`).

Query past incidents **directly** from Alert Bridge — no `/generate`. Two views, one decision
before the first call: a *how many events / times* ask **in a stated period** on VLM real-time
→ block (c), `consolidate=true` with `start_time` + `end_time`, answer = the response `total`;
every other ask → raw (a)/(b). The API does the folding — never merge raw chunks into events
yourself.

**The only parameter that scopes by sensor is `sensor_id`.** Any other spelling (e.g.
`?sensor=`, camelCase `sensorId`) is silently ignored by the API (`realtime_routes.py:577`
declares `sensor_id`; FastAPI drops undeclared params), so `incident_service.py` builds no term
clause and falls through to `match_all` — the request looks sensor-scoped but returns the
**whole store's** total. Scope only with `--data-urlencode "sensor_id=..."`. The same applies to
the window: the declared names are `start_time` and `end_time` (ISO-8601 UTC); `since`, `until`,
`from`/`to` are dropped, and the response then silently covers all time.

**Every `curl` in this workflow is an assertion, not a fetch.** `curl -sf`'s exit status is
swallowed by a `| jq` pipe, and `jq` exits `0` on empty input — so an unreachable Alert
Bridge yields empty/zero output that reads back as a real `count: 0`. Guard each call so an
empty or non-200 body fails loudly instead of being reported as an answer — `jq -e` plus
`|| { echo "...unreachable..."; exit N; }`, where N is the branch's code: `exit 2` where the
unfiltered raw fallback applies ((a), (b), step 3), `exit 1` in (c), where no raw fallback can
answer an events question.

**Keep step 1's resolution and the step 2/3 queries in ONE shell session** so `$NAME`/`$UUID`
persist and the `${VAR:?}` / `|| exit` guards fire — each fenced block in its own Bash call
loses the variables. But this is a **decision tree, not a top-to-bottom script**: run only
ONE of the step-2 queries per the prose ((a) unscoped, (b) name-scoped, or (c) consolidated),
run step 3 **only** when the scoped count is 0, and take the unfiltered fallback **only** when
VIOS is unreachable. The exit code says which branch: `exit 1` / a failed `${VAR:?}` = stop
and tell the user (in (c) this includes an unreachable Alert Bridge); the VIOS-down `exit 2` =
switch to the unfiltered `/incidents` fallback (do **not** report it as an error). The no-colon
`${VAR?}` guards in (c) — and its `${START:?}`/`${END:?}` — are different: they mean *you have
not decided this yet* — resolve the scope or the period from what the user said (possibly to
empty, for scope) and run the block again; they are not a message for the user. The explicit guards do the failure detection — do NOT wrap the
blocks in `set -e`, which (with `pipefail`) would abort the `grep`-no-match branch (an unknown
sensor) before it can tell the user what exists.

An Alert Bridge failure during a raw list or lookup ((a), (b), step 3) is not the VIOS-down
`exit 2` fallback. Re-run the health probe (`curl -sf --max-time 5 "$AB/health"`): if it fails
and `vss-query-analytics` is among your skills, hand the lookup to it; otherwise report the
Alert Bridge error and stop. Never offer or run a deploy from inside Workflow C. If the probe
passes, rerun the request without `-f` to read the HTTP status: a 4xx means the request is
wrong, so fix it; a 5xx means its Elasticsearch backend failed, so report that and stop. An
event count ((c)) cannot be answered without the Alert Bridge.

**Chunks are not events.** RT-VLM writes one document per positive chunk, so one long incident
is several rows. `GET /api/v1/realtime/incidents` has two views — pick one before you query:

- **Raw** (default) → **chunks**. Lists, counts with no period, counts phrased as *incidents /
  alerts*, forensics (`chunk_ids`, on-demand results), and every ask on a CV deployment. On VLM
  real-time report the number as chunks; on CV the rows are verifier / on-demand results —
  report them as incident records, never as chunks or events.
- **Consolidated** (`consolidate=true`; needs `start_time` + `end_time`, else 400) → **events**:
  *consecutive* confirmed RT-VLM chunks with the same `sensorId` + `category` folded together
  (`info.isConsolidated`, `info.chunkCount`, `chunk_ids`). Only for *how many events / times* in
  a **stated** period on VLM real-time. Report the number as events; never invent a window.

Grouping is tuned in `rtvi_vlm.consolidation` (60 s gap / 300 s cap by default); nothing is
deduplicated in the store or the UI.

**If the ask names a sensor, resolve its exact stored name FIRST.** Never derive the value
from the user's phrasing: "the warehouse sample sensor" is English, not an identifier, and
guessing the separator (`warehouse-sample` vs `warehouse_sample`) filters on a value that
does not exist — which returns `count: 0`, not an error.

```bash
# 1. candidate names, from the source of truth. -F matches the wording literally: without it
#    a `.` or `[` in what the user typed is read as a pattern, which quietly matches a
#    different camera or errors out and reads back as "no such sensor".
# Keep the two failures apart: a dead VIOS and an unknown sensor both leave you with no
# name, but one means "use the fallback below" and the other means "tell the user".
LIST=$(vss vios list --type stream) || { echo "VIOS unreachable — exit 2 means: continue with the unfiltered /incidents fallback below (do NOT report an error)"; exit 2; }
# sort -u: one sensor registered twice is one name, not an ambiguous choice between two.
# No separate parse guard: the CLI exits non-zero on a backend failure rather than
# handing back a 200 with a malformed body, so there is no "unparseable data" case
# left to mistake for an empty sensor list.
NAMES=$(printf '%s' "$LIST" | jq -r '.sensors[] | .name')
MATCHES=$(printf '%s' "$NAMES" | grep -Fi -- "<user's wording, e.g. warehouse>" | sort -u)
# Stop unless exactly one name matched — anything else is a question for the user, not a guess
[ "$(printf '%s\n' "$MATCHES" | grep -c .)" = 1 ] || { printf '%s\n' "$MATCHES"; exit 1; }
NAME="$MATCHES"
```

No match means the sensor is not registered: say so and list what exists. Several matches
mean the wording is ambiguous (`warehouse_sample` and `warehouse_sample_2` both contain
"warehouse") — show them and ask which one. Do not take the first: it answers about a
different camera, and its count looks exactly as valid as the right one. Feeding all of them
to the query is worse, because the joined value matches nothing and reads back as `count: 0`.

Fall back to an unfiltered `/incidents` response only when VIOS is unavailable — and fetch it
with the cap, not the browse default: `ALL=$(curl -sf "$AB/api/v1/realtime/incidents?limit=1000" | jq -e .) || { echo "Alert Bridge unreachable — cannot answer"; exit 2; }`.
(The `?limit=20` call in (a) below is for the no-sensor recent-list case; here you need the
whole store to count client-side, so `count == total` can actually hold.) It carries
the same strings, so the values already in it are the candidate list —
`jq -r '.incidents[].sensorId' | sort -u` — and the rule above applies to them unchanged:
exactly one match with the user's wording is the sensor, several is a question for the user,
none means you cannot answer. Never reconstruct the identity by guessing case or separators;
the point of this fallback is that the stored strings are in front of you.

That response is **not** an answer on its own: its `count`/`total` covers every sensor in the
store, so count only the documents carrying the matched value, and say the name could not be
confirmed against VIOS. Counting what came back is only sound while `count == total`.
When they differ the page was truncated: re-request with `--data-urlencode "limit=1000"` (the
endpoint's cap; the default is 100) and page with `offset` if it still truncates. Do not
narrow the asked-for window to make the numbers agree — that answers about a different period
(step 3 states the same "don't narrow the window" rule). If it still truncates at the cap, say the list was cut short and
report the bound, not the number. This list
is weaker than VIOS in one way worth stating to the user: it only contains sensors that have
**produced** incidents. When nothing matches, you cannot tell "this sensor has no incidents"
from "that is not its stored name" — report that ambiguity instead of reporting `0`. For a
(c) ask this raw fallback cannot count events: run (c) with `NAME=` (window, category,
`consolidate=true`), take the `sensorId` strings off the returned events as the candidate list,
apply the same one-match rule, count only the events carrying it — exact only while
`truncated` is `false` AND `count == total` (page with `offset` if the page is short; a
truncated scan makes the figure a lower bound, say so) — and say the name was not confirmed
against VIOS.

```bash
# Each block is its own shell; define what it uses.
# 2. query — run ONE ANSWERING query of these three, never more: the unscoped call answers a
#    different question, and its count is the one that gets misreported as a single sensor's;
#    the consolidated call counts events, the raw calls count chunks. ((c)'s category lookup
#    is a lookup, not an answer.)

# (a) the ask named NO sensor — recent incidents across every sensor
curl -sfG "$AB/api/v1/realtime/incidents" --data-urlencode "limit=20" | jq -e . \
  || { echo "Alert Bridge unreachable — no incidents to report; do NOT read this as empty"; exit 2; }
# windowed ask with no sensor (a period, phrased as incidents/alerts) → add the same window as (b):
#   --data-urlencode "start_time=$START" --data-urlencode "end_time=$END"   (never an invented one)

# (b) the ask named a sensor — scope to it, passing the NAME, not a VIOS UUID.
# Let curl encode it: a name with a space or reserved character breaks a hand-built URL,
# and a mangled value filters on something else (silent zero) instead of erroring.
: "${NAME:?resolve the name first — an empty sensor_id is dropped, not rejected, and the
   response then covers every sensor in the store}"
# Omit start_time/end_time for an all-time count — the endpoint applies NO range filter
# without them. Add them ONLY when the user named a period, and then as real ISO-8601 values,
# never the literal `<ISO>` (which 422s). A window you invent answers about a different period.
curl -sfG "$AB/api/v1/realtime/incidents" \
  --data-urlencode "sensor_id=$NAME" | jq -e . \
  || { echo "Alert Bridge unreachable — no answer; do NOT read this as count 0"; exit 2; }
# windowed ask → add:  --data-urlencode "start_time=$START" --data-urlencode "end_time=$END"

# (c) the ask is HOW MANY EVENTS / TIMES something happened in a STATED period — consolidated
#     view. No period → not (c) (the endpoint answers 400; do not invent a window). A relative
#     period IS a stated period: resolve it on the host clock in UTC and say the bounds you used,
#     e.g. "the last 24 hours" → START=$(date -u -d '-24 hours' +%Y-%m-%dT%H:%M:%SZ); END=$(date -u +%Y-%m-%dT%H:%M:%SZ)
#     (GNU date; on macOS use `date -u -v-24H +%Y-%m-%dT%H:%M:%SZ`)
# Set START/END for THIS ask right here — never reuse bounds left in the shell by an earlier ask.
: "${START:?ISO-8601 start of the period the user named}"
: "${END:?ISO-8601 end of the period the user named}"
# limit=1000 is the page cap (le=1000); total is exact at any limit while truncated=false. If
# count < total the page is incomplete — report total, or page with offset (events never split).
Q=(--data-urlencode "start_time=$START" --data-urlencode "end_time=$END"
   --data-urlencode "consolidate=true" --data-urlencode "limit=1000")
# Both scopes must be DECIDED, never forgotten: `${VAR?}` (no colon) fails when the variable is
# unset and accepts an explicitly empty one. Ask named a sensor → NAME is the resolved name from
# step 1 (same trap as (b): an empty value is dropped, not rejected, and the store-wide event
# count comes back as the sensor's). Ask named NO sensor → set `NAME=` (empty) yourself, so a
# value left over from an earlier ask cannot scope this one.
: "${NAME?decide the sensor scope: the resolved name, or NAME= when the ask names no sensor}"
# Ask named an alert category → it is the stored `category` string (a rule's alert_type),
# copied verbatim. Read the candidates off a raw page of the SAME window and sensor, never guess
# them from English — a failed lookup is a stop, not "no categories"; if .count < .total page
# with offset before deciding:
#   L=(--data-urlencode "start_time=$START" --data-urlencode "end_time=$END" --data-urlencode "limit=1000")
#   [ -n "$NAME" ] && L+=(--data-urlencode "sensor_id=$NAME")
#   RAW=$(curl -sfG --max-time 30 "$AB/api/v1/realtime/incidents" "${L[@]}") || { echo "category lookup failed (HTTP error or unreachable) — stop"; exit 1; }
#   printf '%s' "$RAW" | jq -r '.incidents[].category' | sort -u
# Exactly one match with the user's wording → CATEGORY. Non-empty page but NO match, and NAME
# is the sensor's NAME (a sensor was named and this is the first identity) → the category may
# live under the sensor's other identity (a rule created without sensor_name stores its chunks
# under the VIOS UUID): resolve $UUID exactly as step 3 does and repeat this lookup with
# sensor_id=$UUID; exactly one match there → NAME=$UUID, CATEGORY=<match>, and say the events
# matched the UUID identity. Still no match under either identity → both pages were checked, so
# the answer is 0 events for that wording: report it and list the stored categories the window
# does hold (the user may have meant one of them — offer, do not guess). That 0 is the checked
# answer — no consolidated call, and do not run step 3. Several matches under either identity →
# ask the user which one. NAME= (no sensor named, or the VIOS-down fallback) → there is no other
# identity: no match → report 0 for that wording and list what exists (in the VIOS-down fallback
# that list is store-wide — say the name was not confirmed against VIOS).
# NAME already the UUID (step 3 sent you here, or this branch switched) → you are on the second
# identity: no match → 0 for that wording, list what exists; EMPTY page → set CATEGORY= and run
# the call below, its 0 is the checked answer; never resolve again and do not run step 3 again.
# EMPTY page under the NAME → the answer is 0 events whatever the
# category: set CATEGORY= and continue (step 3 still applies). No category in the ask →
# CATEGORY= (empty).
: "${CATEGORY?decide the category scope: the stored string, or CATEGORY= when the ask names none}"
# Scopes go onto Q only now, after both are decided — so a NAME switched to the UUID above is
# what the request carries.
[ -n "$NAME" ] && Q+=(--data-urlencode "sensor_id=$NAME")
[ -n "$CATEGORY" ] && Q+=(--data-urlencode "category=$CATEGORY")
# No -f: a non-200 body says WHY and is exit 1 — never the VIOS-down fallback, because an
# unfiltered raw list cannot answer an events question. 400 validation_failed = the request
# lacks the window: if the user did state a period the request is wrong, not the deployment —
# set START/END and run this block again; if not, this was never a (c) ask. 422 = request
# validation (a bound is not ISO-8601, limit outside 1..1000, offset < 0) — its body ALSO says
# validation_failed, so branch on $CODE, never on the error token. 5xx = Elasticsearch down.
RESP=$(curl -sG --max-time 30 -w '\n%{http_code}' "$AB/api/v1/realtime/incidents" "${Q[@]}") \
  || { echo "Alert Bridge unreachable or timed out (curl exit $?) — cannot answer an event count; do NOT fall back to the raw list. On a timeout, say the window is too dense for one query and OFFER per-sub-window counts (each with its own bounds — never summed: a boundary-straddling event lands in both)"; exit 1; }
CODE=${RESP##*$'\n'}; BODY=${RESP%$'\n'*}
[ "$CODE" = 200 ] || { echo "HTTP $CODE — $BODY"; exit 1; }
# A 200 is only a consolidated answer if every event carries info.isConsolidated "true" and
# the envelope carries `truncated`: an Alert Bridge that does not know `consolidate` drops
# the parameter and answers with RAW chunks. `truncated` alone is not enough — the schema
# defaults it to false, so a raw body serialized through the schema would carry it too.
printf '%s' "$BODY" | jq -e 'select((.total | type) == "number" and (.truncated | type) == "boolean" and all(.incidents[]; .info.isConsolidated == "true")) | {total, count, truncated}' \
  || { echo "not a consolidated response (raw view or malformed body) — this Alert Bridge cannot answer an event count"; exit 1; }
# total = events in the window; exact whenever truncated=false. truncated=true → the window
# held more chunks than the service's scan cap (the 10000 newest were kept, older dropped):
# report total as "at least", say the window is too dense, and offer per-sub-window counts
# (each reported with its own bounds — never summed, since a boundary-straddling event lands
# in both). Per event: the
# timestamp..end span, info.chunkCount chunks (info values are STRINGS — `tonumber` before
# summing), chunk_ids for the raw rows; info.chunkIdx is the representative chunk's,
# info.chunkIdxRange the span.

# 3. a scoped `count: 0` is not an answer yet: a rule created without `sensor_name` stores the
#    stream id instead, so the rows exist under the UUID. There are only these two identities
#    to try — ask about the second one directly. (Skip this step when NAME is already the UUID:
#    (c)'s category lookup may have switched identities for you, and that zero is checked.) `total` is the full match count, so this is
#    exact at any `limit`, and needs no paging through the store.
UUID=$(vss vios list --type stream --sensor "$NAME" | jq -r 'first(.sensors[] | select(.is_main) | .sensor_id) // empty' | sort -u)
# same trap as $NAME, and it springs while you are being careful: if VIOS died or dropped the
# sensor since step 1, an empty $UUID is dropped from the query and the store-wide total comes
# back as this sensor's — turning "none" into someone else's incidents.
: "${UUID:?VIOS no longer resolves this sensor — say the alternate identity could not be checked}"
# Same dedup as step 1 (${UUID:?} only tests emptiness): a two-line $UUID goes on the wire as
# sensor_id=<uuid>%0A<uuid> and matches nothing — collapse it; if two distinct ids remain, ask.
[ "$(printf '%s\n' "$UUID" | grep -c .)" = 1 ] || { printf '%s\n' "$UUID"; exit 1; }
# Carry the SAME view and window as step 2. After (b): omit start_time/end_time for an
# all-time count, or add the SAME window the user asked for. After (c): do NOT reuse the call
# below — `unset CATEGORY; NAME=$UUID` and re-run block (c) with the same START/END, INCLUDING
# its category lookup (the unset makes the `${CATEGORY?}` guard fire again): a CATEGORY= that
# came from an empty name-scoped page must not be carried over, or every category under the
# UUID gets reported as the one the user named. Its total is the event count under the UUID
# identity. Mismatching step 2 answers a different question — the
# endpoint applies no range filter without the window, so an all-time total comes back for a
# "today" ask, and a raw retry after (c) counts chunks where the user asked for events.
TOTAL=$(curl -sfG "$AB/api/v1/realtime/incidents" \
  --data-urlencode "sensor_id=$UUID" | jq -e '.total') \
  || { echo "Alert Bridge unreachable — the alternate-identity check did not run; do NOT report a zero"; exit 2; }
# windowed ask → add the same:  --data-urlencode "start_time=$START" --data-urlencode "end_time=$END"
# jq -e exits non-zero on null/absent output, so an empty body (Alert Bridge down) fails the
# assignment rather than yielding "" that reads back as a checked zero.
# $TOTAL > 0 → that is the answer; say it matched the sensor's UUID, not its name. Exactly 10000 is
#   the one number to distrust in the RAW view (a/b): it never asks Elasticsearch for an exact
#   hit count (its 10000 threshold), and paging cannot go past it either, so 10000 is a floor.
#   Report it as "at least 10000" — that is the true answer, not a fallback. Only narrow the
#   window if the user asks for a finer figure, and then say which window the new number belongs
#   to. A consolidated total (c) is exact whenever `truncated` is false; there the flag, not the
#   number, is what to read.
# 0 as well → both identities are empty, so "none found" is now a checked answer.
```

> **`sensor_id` here filters on a stored value, not on a VIOS UUID.** It is an exact
> term match (case-sensitive) on whatever the incident document carries in `sensorId`, and
> RT-VLM fills that field by precedence **`camera_id` → `sensor_name` → stream id**
> (`rtvi_stream_handler.py`). Through the Workflow D path Alert Bridge sends `sensor_name`
> and never `camera_id`, so a rule created the documented way yields the **sensor name**
> (`warehouse_sample`, `sample-warehouse-ladder`; `ondemand` for Workflow F results). One
> case legitimately holds something else: a rule created **without** `sensor_name` falls back
> to the stream id — the VIOS UUID. `camera_id` outranks the name in that expression but is
> not a third value to hunt for: every VIOS registration path sets `sensor_name` and
> `camera_id` from the same field (`rtvi_embed_server.py`), so it resolves to the string the
> name lookup already returns. Two identities, both reachable from `sensor/list` — step 3
> below tries the second one. Only the whitespace is stripped:
> no lowercasing, and interior spaces survive, which is why the query parameter must be
> URL-encoded.
>
> **Copy the value verbatim — never normalise it.** Paste the exact string the sensor list
> or the incident document returned: do not swap `_` for `-` (or the reverse), do not change
> case, do not strip a suffix. `warehouse_sample` and `warehouse-sample` are two different
> values to a term match, and the wrong one returns `count: 0` rather than an error — so a
> one-character slip reads back as "no incidents" and there is nothing in the response to
> tell you it was a typo. This is the opposite of Workflow D, where the rule-create payload's
> `sensor_id` **must** be the VIOS UUID.

Response is an `IncidentListResponse`: `{ "status", "incidents": [...], "count", "total", "timestamp" }` (the schema also declares `truncated`, default `false`; raw responses omit it). In the raw view `total` is Elasticsearch's thresholded hit count: exact below 10000, saturating at it, and nothing in the raw response tells those two apart — so exactly 10000 is a lower bound, not a count. Summarize each incident's timestamp, sensor (report `sensorId` as returned — usually the name, no reverse lookup needed), and category. **Run the query — never answer from memory.** An **empty `incidents` list is a valid answer once it has been checked** — when the ask named a sensor, a zero scoped by the NAME means *not under this identity*, so run step 3 before reporting it; a zero already scoped by the UUID (step 3 or (c)'s category lookup switched you) is the checked answer — do not run step 3 again. Then report "none found / count 0" and STOP; do not fall back to listing rules. When the ask named a sensor, the count you report is the **scoped** one: quote **`total`** from the response you filtered by the identity you confirmed — the name, or the UUID that step 3 or (c)'s category lookup matched; or the 0 that (c)'s category lookup established under both identities — and say which sensor, and which identity, it belongs to. `total` is how many matched; `count` is how many came back in the page you asked for, and it stops at `limit` (100 by default), so quoting it turns 500 incidents into 100 without any sign that it did. A `0` read off the unfiltered query answers a different question — and it is also what a mistyped name returns, so neither you nor the reader can tell the two apart afterwards.

With `consolidate=true` the same envelope carries `truncated`, and the numbers change meaning: `total` is the number of **events** in the window (exact whenever `truncated` is `false`), `count` the events on the page, and each event carries `info.isConsolidated: "true"`, `info.chunkCount` (a string), `chunk_ids`, and a `timestamp`..`end` span. Report it as events and name the window — "2 intrusion events on `warehouse_sample` between 10:00 and 10:30 UTC (4 chunks)" — never as a chunk count, and never mix the two views in one figure. `truncated: true` makes `total` a lower bound; say so.
