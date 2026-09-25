#!/usr/bin/env bash
# =============================================================================
#  INTERNAL TEST HARNESS. Not product, not documentation, not shipped.
#  Lives only on branch test/mv3dt-docs and is gitignored everywhere else.
#  Never change anything here to make an external-facing bug go away: a fix that
#  only quiets the harness leaves the defect in place for whoever reported it.
#  Those belong in services/rtvi/rt-cv-3d/... or in the component's shipped docs.
#  Do not cite this file in bug comments, Slack or email. Nobody outside this
#  workspace has it, so guidance that leans on it cannot be acted on.
# =============================================================================
# Set VST tiles per row on a running vss-vios-ingress, no rebuild.
# Re-derives from the pristine image bundle every run, so the current value never matters.
set -euo pipefail

PER_ROW="${1:?usage: vst-tiles.sh <per-row: 1 2 3 4 6 12> [live|replay|both]  (default live)}"
WHICH="${2:-live}"
CT="${VST_INGRESS:-vss-vios-ingress}"

(( 12 % PER_ROW == 0 )) || { echo "per-row must divide 12 (1 2 3 4 6 12)" >&2; exit 1; }
MD=$(( 12 / PER_ROW ))

IMG=$(docker inspect -f '{{.Config.Image}}' "$CT")
ASSET=$(docker exec "$CT" sh -c 'ls /vst-ui/assets/js/main-*.js' | tr -d '\r')

CID=$(docker create "$IMG"); trap 'docker rm -f "$CID" >/dev/null 2>&1 || true' EXIT
docker cp "$CID:$ASSET" /tmp/.vst-pristine.js

MD="$MD" WHICH="$WHICH" python3 - /tmp/.vst-pristine.js /tmp/.vst-patched.js <<'PY'
import os, re, sys
md, which = os.environ['MD'], os.environ['WHICH'].lower()
want = {'live': ['Live'], 'replay': ['Replay'], 'both': ['Live', 'Replay']}[which]
src = open(sys.argv[1], encoding='utf-8').read()
pat = re.compile(r'(\{xs:12,md:)(\d+)(\},children:\(0,[\w$]+\.jsx\)\([\w$]+,\{sensor:e,streamType:[\w$]+\.)(Live|Replay)')
hits = []
def sub(m):
    if m.group(4) in want:
        hits.append((m.group(4), m.group(2)))
        return m.group(1) + md + m.group(3) + m.group(4)
    return m.group(0)
out = pat.sub(sub, src)
if len(hits) != len(want):
    sys.exit(f"expected {len(want)} match(es) for {want}, found {len(hits)} - bundle layout changed, patch aborted")
open(sys.argv[2], 'w', encoding='utf-8').write(out)
print("  patched: " + ", ".join(f"{v} md:{o} -> md:{md}" for v, o in hits))
PY

docker cp /tmp/.vst-patched.js "$CT:$ASSET"
echo "  $PER_ROW per row on $WHICH. Bundle: $ASSET"
echo "  Browser still caches it: DevTools > Network > Disable cache, or use incognito."
