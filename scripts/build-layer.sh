#!/usr/bin/env bash
# Empaqueta las dependencias de la Lambda layer con uv nativo (sin requirements.txt).
# Resultado: app/layers/dependencias/python/<paquetes> (formato Lambda layer: prefijo "python/")
set -euo pipefail
cd "$(dirname "$0")/../app/layers/dependencias"
rm -rf python
uv pip install --no-deps --target python -r <(uv export --no-hashes --no-dev)
echo "Layer construida en: $(pwd)/python"
