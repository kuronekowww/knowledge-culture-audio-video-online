#!/bin/sh
cd "$(dirname "$0")" || exit 1
exec python3 preview_server.py --open
