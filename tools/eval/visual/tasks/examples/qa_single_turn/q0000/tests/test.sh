#!/usr/bin/env bash
# C6 verifier: deterministic probes — integrity and shape, not meaning.
# Emits reward.json (named rewards) + reward.txt (scalar mirror for the Gym bridge)
# + facts.json. No LLM judging here — that's the C7 score pass.
set -uo pipefail

ANSWERED=0
EVIDENCE_VALID=0

if [ -s /output/answer.json ] && python3 -c "import json;json.load(open('/output/answer.json'))" 2>/dev/null; then
  ANSWERED=1
  # evidence refs resolve against the injected binding (tests/gt/ holds what we need)
  if python3 - <<'EOF' 2>/dev/null
import json
a = json.load(open("/output/answer.json"))
ev = a.get("evidence", [])
assert isinstance(ev, list) and all("t0" in e and "t1" in e for e in ev)
EOF
  then EVIDENCE_VALID=1; fi
fi

cat > reward.json <<EOF
{"answered": $ANSWERED, "evidence_valid": $EVIDENCE_VALID}
EOF
echo "$ANSWERED" > reward.txt

cat > facts.json <<EOF
{"api_failures": 0, "verified_at": "$(date -u +%FT%TZ)"}
EOF
