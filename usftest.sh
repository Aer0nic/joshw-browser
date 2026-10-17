#!/bin/bash

# Cleanup function to kill background processes and remove temp files on exit
cleanup() {
    jobs -p | xargs kill -9 2>/dev/null
    pkill -9 -f "lazyusf" 2>/dev/null
    rm -f "$FIFO" "$ERR_LOG" 2>/dev/null
}

trap cleanup EXIT INT TERM

# Function to open a graphical file browser for selection
select_file() {
    if command -v zenity &>/dev/null; then
        zenity --file-selection --title="Select USF Music File" --filename="$HOME/" 2>/dev/null
        return
    fi

    if command -v kdialog &>/dev/null; then
        kdialog --getopenfilename "$HOME" "*.miniusf *.usf" 2>/dev/null
        return
    fi

    read -p "Enter path to song (.miniusf or .usf): " fallback_path
    echo "$fallback_path"
}

# Open the file browser
SONG_FILE=$(select_file)

# Clean any leading/trailing wrapping quotes safely
if [[ "$SONG_FILE" == \"*\" ]]; then
    SONG_FILE="${SONG_FILE#\"}"
    SONG_FILE="${SONG_FILE%\"}"
elif [[ "$SONG_FILE" == \'*\' ]]; then
    SONG_FILE="${SONG_FILE#\'}"
    SONG_FILE="${SONG_FILE%\'}"
fi

if [ -z "$SONG_FILE" ] || [ ! -f "$SONG_FILE" ]; then
    echo "Error: No file selected or file does not exist."
    exit 1
fi

LAZYUSF_BIN="$HOME/.config/joshw/lazyusf"

# 1. Make sure to kill any running lazyusf instances first
echo "Cleaning up any running lazyusf processes..."
pkill -9 -f "lazyusf" 2>/dev/null

# Verify lazyusf exists
if [ ! -x "$LAZYUSF_BIN" ]; then
    echo "Error: lazyusf not found or not executable at $LAZYUSF_BIN"
    exit 1
fi

SONG_DIR=$(dirname "$SONG_FILE")
SONG_NAME=$(basename "$SONG_FILE")

# 2. PROBE STEP: Quickly grab metadata without blocking on stdout
echo "Fetching song metadata..."
ERR_LOG=$(mktemp)
(
    cd "$SONG_DIR" || exit 1
    "$LAZYUSF_BIN" "$SONG_NAME" > /dev/null 2> "$ERR_LOG"
) &
PROBE_PID=$!

RATE=32000

# Check log for metadata for up to 1 second
for i in {1..20}; do
    if grep -q "LAZYUSF_META:" "$ERR_LOG"; then
        META_LINE=$(grep "LAZYUSF_META:" "$ERR_LOG" | tail -n 1)
        IFS=':' read -r _ detected_rate _ <<< "$META_LINE"
        if [ -n "$detected_rate" ]; then
            RATE="$detected_rate"
        fi
        break
    fi
    sleep 0.05
done

# Kill the probe process explicitly and clean its temp log
kill -9 "$PROBE_PID" 2>/dev/null
rm -f "$ERR_LOG"

# FORCED FOR TESTING: Always use s16be
FORMAT="s16be"

echo "Detected Native Rate: $RATE Hz | Forced Format: $FORMAT"
echo "Playing... Press Ctrl+C to stop."

# 3. PLAYBACK STEP: Create FIFO and start lazyusf feeding directly into mpv
FIFO=$(mktemp -u)
mkfifo "$FIFO"

(
    cd "$SONG_DIR" || exit 1
    "$LAZYUSF_BIN" "$SONG_NAME" > "$FIFO" 2>/dev/null
) &
LAZY_PID=$!

# Play the stream in mpv using the gathered metadata
mpv --demuxer=rawaudio \
    --demuxer-rawaudio-rate="$RATE" \
    --demuxer-rawaudio-channels=2 \
    --demuxer-rawaudio-format="$FORMAT" \
    --cache=yes \
    --cache-secs=4 \
    --audio-buffer=0.5 \
    "$FIFO"
