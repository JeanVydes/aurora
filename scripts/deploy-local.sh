#!/usr/bin/env bash
# LocalStack CDK helper — run synth/bootstrap/deploy from the HOST.
# The bundled cdk-dev container is NOT used: running CDK from the host matches
# what works out of the box on Debian/Ubuntu/NixOS/macOS (publishes 4566 on localhost).
#
# Usage:
#   scripts/deploy-local.sh bootstrap                 # first time on a host
#   scripts/deploy-local.sh deploy --require-approval never
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$ROOT/.venv/bin:$PATH"
export JSII_RUNTIME_PACKAGE_CACHE_ROOT="${JSII_RUNTIME_PACKAGE_CACHE_ROOT:-/tmp/jsii-cache-$UID}"
mkdir -p "$JSII_RUNTIME_PACKAGE_CACHE_ROOT"

# Synth output MUST NOT land in app/cdk.out — on some trees it is unwritable
# (root-owned from past root runs). Use a fresh temp dir unless caller passed -o.
OUTPUT_DIR="$(mktemp -d /tmp/aurora-synth-XXXX)"
cd "$ROOT/app"
if printf '%s\n' "$*" | grep -qE '(^|[[:space:]])(-o|--output)(=|[[:space:]]|$)'; then
  npx -y -p aws-cdk -p aws-cdk-local cdklocal "$@"
else
  # ensure $ROOT/.venv/bin/python3 has aws-cdk-lib (uv-managed)
  npx -y -p aws-cdk -p aws-cdk-local cdklocal "$@" -o "$OUTPUT_DIR"
fi
