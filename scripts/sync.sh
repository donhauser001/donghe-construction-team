#!/bin/sh
set -eu
DONGHE_PACKAGE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ ! -x "$DONGHE_PACKAGE_ROOT/runtime/python/bin/python3" ]; then
  printf '%s\n' 'Source checkout is not a complete package. Build the platform release, then run its bin/install; do not overwrite installed runtimes with source-only files.' >&2
  exit 1
fi
exec "$DONGHE_PACKAGE_ROOT/bin/install" "$@"
