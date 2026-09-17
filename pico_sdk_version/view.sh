#!/usr/bin/env bash
#
# The viewer moved to the shared receiver/ directory, so that all four variants
# of the thermal camera use the same host side tool. This wrapper stays behind
# for the paths and habits the pico_sdk_version README used to describe.
#
#   ./view.sh --colormap turbo
#   ./view.sh --host 192.168.1.42 --port 4242      # a board streaming over WiFi
#
# Any argument is passed straight through to receiver/view.sh.
#
set -euo pipefail

cd "$(dirname "$0")"

exec ../receiver/view.sh "$@"