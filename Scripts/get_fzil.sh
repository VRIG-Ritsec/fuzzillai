#!/bin/bash

# Decode all .b64 files and lift to JavaScript

# Step 1: Decode all .b64 files to .fzil
echo "=== Decoding .b64 files ==="
for file in *.b64; do
    # Check if any .b64 files exist
    if [ ! -e "$file" ]; then
        echo "No .b64 files found in current directory"
        exit 1
    fi
    
    # Get the base filename without extension
    basename="${file%.b64}"
    
    # Decode the file
    echo "Decoding $file to ${basename}.fzil"
    cat "$file" | base64 --decode > "${basename}.fzil"
done

echo -e "\n=== Lifting .fzil files to JavaScript ==="

# Step 2: Lift all .fzil files to JavaScript
for fzil_file in *.fzil; do
    # Check if any .fzil files exist
    if [ ! -e "$fzil_file" ]; then
        echo "No .fzil files found"
        exit 1
    fi
    
    echo "Processing $fzil_file"
    ~/fuzzillai/.build/debug/FuzzILTool --liftToJS "$fzil_file"
done

echo -e "\nAll files processed successfully!"
