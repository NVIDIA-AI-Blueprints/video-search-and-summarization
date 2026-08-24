#!/usr/bin/env bash
# Apply or destroy the infrastructure stack.
set -euo pipefail

ACTION="${1:-apply}"
cd "$(dirname "$0")/../terraform"

case "$ACTION" in
  apply)
    test -f tfplan || { echo "No tfplan found; run bootstrap.sh first"; exit 1; }
    terraform apply tfplan
    ;;
  destroy)
    terraform destroy
    ;;
  *)
    echo "Usage: $0 [apply|destroy]"; exit 1
    ;;
esac
