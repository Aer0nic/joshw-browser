#!/bin/bash
set -e

echo "=== Building Original Unpatched LazyUSF ==="

# Create a temporary working directory
TEMP_DIR=$(mktemp -d -t lazyusf_orig_XXXXXX)
cd "$TEMP_DIR"

echo "Cloning repository..."
git clone https://github.com/derselbst/lazyusf.git
cd lazyusf

echo "Configuring with CMake..."
mkdir build
cd build
cmake ..

echo "Compiling..."
make

# Define target directory
TARGET_DIR="$HOME/github/joshw-browser"
mkdir -p "$TARGET_DIR"

echo "Copying binary to $TARGET_DIR..."
find . -name "lazyusf" -type f -exec cp {} "$TARGET_DIR/" \;
chmod +x "$TARGET_DIR/lazyusf"

# Cleanup temporary build files
cd /
rm -rf "$TEMP_DIR"

echo "=================================================="
echo "Success! Unpatched lazyusf is located at:"
echo "$TARGET_DIR/lazyusf"
echo "=================================================="
