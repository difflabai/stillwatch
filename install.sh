#!/bin/sh
set -eu
STILLWATCH_SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PYTHONPATH="$STILLWATCH_SOURCE_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
exec /usr/bin/python3 -m stillwatch install "$@"
