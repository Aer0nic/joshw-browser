#!/bin/bash

# Usage: ./search_files.sh [directory] [search_term] [path_to_downloaders.py]
SEARCH_DIR="${1:-.}"
SEARCH_TERM="${2:-r4300}"
DOWNLOADERS_PY="${3:-downloaders.py}"
CACHE_DIR="$HOME/.config/joshw"

if [ ! -d "$SEARCH_DIR" ]; then
    echo "Error: Directory '$SEARCH_DIR' does not exist."
    exit 1
fi

echo "=================================================="
echo "Searching for '$SEARCH_TERM' in: $(realpath "$SEARCH_DIR")"
echo "=================================================="

# Run grep to find matching lines and files
grep -rnI --color=auto "$SEARCH_TERM" "$SEARCH_DIR"

echo "=================================================="
echo "Analyzing CMake targets in $DOWNLOADERS_PY..."
echo "=================================================="

if [ ! -f "$DOWNLOADERS_PY" ]; then
    echo "Error: Could not find '$DOWNLOADERS_PY' in the current directory."
    exit 1
fi

# Extract all currently used .c files from any add_library(lazyusf_shared SHARED ...) lines
CURRENT_C_FILES=$(grep "add_library(lazyusf_shared" "$DOWNLOADERS_PY" | grep -oE '[a-zA-Z0-9_-]+\.c' | sort -u)

# Find all .c files that contain the search term in the source directory
MATCHING_FILES_PATH=$(grep -rnI -l "$SEARCH_TERM" "$SEARCH_DIR"/*.c 2>/dev/null)
MATCHING_C_FILES=$(basename -a $MATCHING_FILES_PATH 2>/dev/null | sort -u)

# Compare and find missing ones
MISSING_FILES=()
for file in $MATCHING_C_FILES; do
    if ! echo "$CURRENT_C_FILES" | grep -q "^$file$"; then
        MISSING_FILES+=("$file")
    fi
done

if [ ${#MISSING_FILES[@]} -gt 0 ]; then
    echo -e "\n\033[1;31m[!] Missing .c files detected containing '$SEARCH_TERM':\033[0m"
    for m in "${MISSING_FILES[@]}"; do
        echo "  - $m"
    done

    # Build the combined list of all .c files
    ALL_FILES_COMBINED=$(echo "$CURRENT_C_FILES ${MISSING_FILES[*]}" | tr ' ' '\n' | sort -u | tr '\n' ' ')

    echo -e "\n\033[1;33m[*] Automatically updating $DOWNLOADERS_PY with the new comprehensive list...\033[0m"

    # Use Python to safely replace the target definitions in downloaders.py
    python3 -c "
import pathlib, re

d_path = pathlib.Path('$DOWNLOADERS_PY')
content = d_path.read_text(encoding='utf-8')
all_files_str = '$ALL_FILES_COMBINED'

pattern = r'(add_library\s*\(\s*lazyusf_shared\s+SHARED\s+)([^)]+)(\))'

def replacer(match):
    prefix = match.group(1)
    files = sorted(list(set(all_files_str.split())))
    return prefix + ' '.join(files) + match.group(3)

new_content, count = re.subn(pattern, replacer, content)
if count > 0:
    d_path.write_text(new_content, encoding='utf-8')
    print(f'Successfully updated {count} location(s) in $DOWNLOADERS_PY!')
else:
    print('Error: Could not locate lazyusf_shared pattern for replacement.')
"
    echo -e "\n\033[1;32m[✓] downloaders.py has been successfully updated!\033[0m"
else
    echo -e "\n\033[1;32m[✓] All matching .c files for '$SEARCH_TERM' are already present in downloaders.py. No changes needed.\033[0m"
fi

echo "=================================================="
echo "Cleaning old build artifacts & caches from $CACHE_DIR..."
echo "=================================================="

# Clear out cached lazyusf binaries and temp build trees
rm -rf "$CACHE_DIR/lazyusf" "$CACHE_DIR/liblazyusf.so" "$CACHE_DIR/lazyusf_shared"

echo -e "\n\033[1;32m[✓] Cache cleared successfully! Ready to rebuild.\033[0m"
echo "=================================================="
