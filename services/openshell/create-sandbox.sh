#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# README steps 1b, 3 and 4 with the VSS layer, against a gateway Compose
# already started: register the gateway, seal the credential, replace the
# sandbox, point its `vss` CLI at the deployment. Each run recreates the
# sandbox, since neither the image nor the policy can change on a running one.
#
# Expects the openshell CLI (README step 0) and the two images (step 2 and the
# VSS layer's step 1) on this host.
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
set -a; . "$here/../../deploy/docker/services/openshell/openshell.env"; set +a

name=${OPENSHELL_SANDBOX_NAME:-hermes}
provider=${HARNESS_PROVIDER:-agent-inference}
# VSS_REF may be a branch; a Docker tag cannot hold "/".
image=openshell-hermes-vss:$(printf %s "$VSS_REF" | tr / -)-from-source

gateway=${OPENSHELL_GATEWAY_URL:-http://127.0.0.1:8080}
openshell gateway list 2>/dev/null | grep -q " $gateway " ||
  openshell gateway add "$gateway" --local --name openshell-gateway

# `gateway add` only warns when the endpoint is cold, and Compose returns
# before the gateway binds its listener.
i=0
until openshell sandbox list >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -lt 30 ] || { echo "no gateway at $gateway" >&2; exit 1; }
  sleep 2
done

openshell profile describe "$provider" >/dev/null 2>&1 ||
  openshell profile import -f "$here/providers/$provider-profile.yaml"
if openshell provider get "$provider" >/dev/null 2>&1; then
  openshell provider update "$provider" --credential HARNESS_API_KEY
else
  openshell provider create --name "$provider" \
    --type "$provider" --credential HARNESS_API_KEY
fi

openshell sandbox delete "$name"
openshell sandbox create \
  --name "$name" \
  --from "$image" \
  --policy "$here/vss/policy.yaml" \
  --provider "$provider" \
  --env VSS_PUBLIC_URL="$VSS_PUBLIC_URL" \
  --env HARNESS_BASE_URL="$HARNESS_BASE_URL" \
  --env HARNESS_MODEL="$HARNESS_MODEL" \
  --restart-policy on-failure \
  --expose 8642 --expose-authorization-mode bearer-passthrough --detach \
  -- sh -c 'chmod 770 /sandbox;
            trust-supervisor-ca
            C=/sandbox/.hermes/config.yaml
            sed -i -e "s#\${env:HARNESS_BASE_URL}#$HARNESS_BASE_URL#g" \
                   -e "s#\${env:HARNESS_MODEL}#$HARNESS_MODEL#g" "$C"
            E=/sandbox/.hermes/.env
            grep -q "^API_SERVER_KEY=" "$E" 2>/dev/null || {
              K=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d " \n")
              printf "API_SERVER_KEY=%s\n" "$K" >> "$E"; }
            chmod 640 "$E"
            exec hermes gateway run'

openshell sandbox exec -n "$name" -- vss configure --base-url "$VSS_PUBLIC_URL"
openshell sandbox exec -n "$name" -- vss-hermes-sync
