#!/usr/bin/env bash
# Bootstrap Terraform state backend and plan the stack.
set -euo pipefail

cd "$(dirname "$0")/../terraform"

terraform init
terraform fmt -recursive
terraform validate
terraform plan -out=tfplan

echo "Review tfplan, then run: ./scripts/deploy.sh apply"
