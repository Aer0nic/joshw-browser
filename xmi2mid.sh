#!/usr/bin/env bash

folder="${1:-}"

if [[ -z "$folder" || ! -d "$folder" ]]; then
    echo "Usage: $0 DIRECTORY"
    exit 1
fi

mapfile -d '' files < <(find "$folder" -type f -iname '*.xmi' -print0)

if ((${#files[@]} == 0)); then
    echo "No .xmi files found in: $folder"
    exit 1
fi

# Don't overwrite any existing MIDI files.
for file in "${files[@]}"; do
    output="${file%.*}.mid"
    if [[ -e "$output" ]]; then
        echo "Output already exists; nothing converted or deleted: $output"
        exit 1
    fi
done

# Convert all files. Keep originals if any conversion fails.
for file in "${files[@]}"; do
    output="${file%.*}.mid"
    echo "Converting: $file -> $output"
    if ! wildmidi -x "$output" "$file"; then
        echo "Conversion failed; XMI originals have been kept."
        exit 1
    fi
done

# Delete originals only after every conversion succeeds.
for file in "${files[@]}"; do
    rm -- "$file"
done

echo "Conversion succeeded; XMI originals deleted."

