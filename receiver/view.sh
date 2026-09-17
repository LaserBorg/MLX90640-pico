#!/usr/bin/env bash
#
# Launch the thermal camera viewer.
#
# Sets up the Python environment on first use, finds the board's USB serial port
# automatically, and starts viewer.py. Any extra arguments are passed through,
# so for example:
#
#   ./view.sh --colormap turbo
#   ./view.sh --host 192.168.1.42 --port 4242      # a board streaming over WiFi
#
# An existing interpreter can be reused instead of building a virtual
# environment, which is useful when one is already set up in the repository:
#
#   VIEWER_PYTHON=../.venv/bin/python ./view.sh
#
set -euo pipefail

cd "$(dirname "$0")"

PORT="${VIEWER_PORT:-}"
REQUIRED="import cv2, numpy, serial"

# ---- Python environment ---------------------------------------------------

# An interpreter that already has the dependencies wins: nothing to set up.
if [ -n "${VIEWER_PYTHON:-}" ]; then
    PYTHON="$VIEWER_PYTHON"
    if ! "$PYTHON" -c "$REQUIRED" 2>/dev/null; then
        echo "Error: $PYTHON cannot import cv2, numpy and serial." >&2
        echo "Install them into it, or unset VIEWER_PYTHON to build a .venv here." >&2
        exit 1
    fi
else
    # A virtual environment of our own, or one belonging to the repository
    # (view.sh was moved here from pico_sdk_version/, where it used to sit next
    # to the build, and the repository root has had one for a while).
    for candidate in ../.venv .venv; do
        if [ -x "$candidate/bin/python" ] && "$candidate/bin/python" -c "$REQUIRED" 2>/dev/null; then
            # Resolved, because Python compares sys.prefix against the path it
            # was started with and complains about a relative one.
            PYTHON="$(cd "$candidate" && pwd)/bin/python"
            break
        fi
    done

    if [ -z "${PYTHON:-}" ]; then
        echo "Creating the virtual environment in .venv ..."
        python3 -m venv .venv
        PYTHON=".venv/bin/python"
    fi

    if ! "$PYTHON" -c "$REQUIRED" 2>/dev/null; then
        echo "Installing dependencies (opencv-python numpy pyserial) ..."
        "$PYTHON" -m pip install --quiet --upgrade pip
        "$PYTHON" -m pip install --quiet -r requirements.txt
    fi
fi

# Qt needs a font to draw the overlay text; the one bundled with OpenCV is an
# empty placeholder, so borrow a system font if there is one.
FONT_DIR="$("$PYTHON" -c 'import cv2,os;print(os.path.join(os.path.dirname(cv2.__file__),"qt","fonts"))' 2>/dev/null || true)"
if [ -n "$FONT_DIR" ] && [ -d "$FONT_DIR" ] && [ -z "$(ls -A "$FONT_DIR" 2>/dev/null)" ]; then
    for f in /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf \
             /usr/share/fonts/TTF/DejaVuSans.ttf \
             /usr/share/fonts/dejavu/DejaVuSans.ttf; do
        if [ -f "$f" ]; then cp "$f" "$FONT_DIR"/ 2>/dev/null && break; fi
    done
fi

# ---- Work out where the stream is -----------------------------------------

# If the user already said --device/--host, do not guess.
if [ "$#" -eq 0 ] || ! printf '%s\n' "$@" | grep -qE -- '--device|-d$|--host|-H$'; then
    if [ -z "$PORT" ]; then
        # Look for a board in application mode and take its first serial
        # interface. ttyACM0 is the port the firmware streams on.
        for candidate in /dev/ttyACM0 /dev/ttyACM1 /dev/ttyUSB0; do
            if [ -e "$candidate" ]; then
                PORT="$candidate"
                break
            fi
        done
    fi

    if [ -z "$PORT" ] || [ ! -e "$PORT" ]; then
        echo "Error: no serial port found." >&2
        echo "Is the board plugged in and running the thermal camera firmware?" >&2
        echo "Available ports:" >&2
        ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null >&2 || echo "  (none)" >&2
        exit 1
    fi
    set -- --device "$PORT" "$@"
    echo "Using $PORT"
fi

# ---- Run ------------------------------------------------------------------

exec "$PYTHON" viewer.py "$@"