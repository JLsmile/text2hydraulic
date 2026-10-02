#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
# Read-only diagnostics; dependency installation is always manual.
python3 setup_renderer.py --check || true
exec python3 server.py "$@"
