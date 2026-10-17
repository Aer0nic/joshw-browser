#!/bin/bash

# 1. Locate lazyusf in the expected config path
LAZYUSF_BIN="$HOME/.config/joshw/lazyusf"

if [ ! -f "$LAZYUSF_BIN" ]; then
    echo "Error: lazyusf binary not found at $LAZYUSF_BIN"
    exit 1
fi

# 2. Open a graphical file browser window to select the .miniusf/.usf file
if command -v zenity &> /dev/null; then
    SELECTED_FILE=$(zenity --file-selection --title="Select a .miniusf or .usf file" --file-filter="USF Files | *.miniusf *.usf")
else
    echo "Error: zenity is required for the graphical file picker."
    exit 1
fi

if [ -z "$SELECTED_FILE" ]; then
    echo "Selection cancelled."
    exit 0
fi

WORK_DIR=$(dirname "$SELECTED_FILE")
FILENAME=$(basename "$SELECTED_FILE")

# 3. Create a secure temporary directory and a named pipe (FIFO)
FIFO_DIR=$(mktemp -d -p /tmp lazyusf_test_fifo_XXXXXX)
FIFO_PATH="$FIFO_DIR/stream.au"

mkfifo "$FIFO_PATH"
echo "Created FIFO pipe at: $FIFO_PATH"

# Cleanup trap to ensure background processes and temp files are wiped on exit
cleanup() {
    echo -e "\nCleaning up processes and temporary files..."
    kill "$LAZY_PID" 2>/dev/null
    kill "$MPV_PID" 2>/dev/null
    rm -rf "$FIFO_DIR"
    exit 0
}
trap cleanup EXIT INT TERM

# 4. Start lazyusf and let its output print to the terminal so we can see any errors
echo "Starting lazyusf rendering for: $FILENAME"
cd "$WORK_DIR" || exit 1

# Try running it normally with just the filename first to see what it outputs:
"$LAZYUSF_BIN" "$FILENAME" &
LAZY_PID=$!

# 5. Start mpv in the background, listening to the FIFO pipe
echo "Starting mpv..."
mpv --no-video --really-quiet "$FIFO_PATH" &
MPV_PID=$!

echo "Streaming active! Monitoring FIFO file data flow..."
echo "Press Ctrl+C to stop."
echo "--------------------------------------------------"

# 6. Real-time terminal monitor loop for the FIFO stream
while kill -0 "$LAZY_PID" 2>/dev/null; do
    if [ -e "$FIFO_PATH" ]; then
        # Check bytes read/stat info (Note: FIFOs often report 0 size on disk 
        # because data passes straight through memory buffers to mpv, but we can 
        # check file status modifications or process health)
        STATUS=$(ls -la "$FIFO_PATH")
        echo "[MONITOR] Pipe status: $STATUS"
    else
        echo "[MONITOR] FIFO path vanished!"
        break
    fi
    sleep 1
done

echo "lazyusf process has finished or exited."
wait