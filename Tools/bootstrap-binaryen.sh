#!/bin/bash
# Tools/bootstrap-binaryen.sh
set -e
set -o pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHA_FILE="$REPO_ROOT/binaryen_commit.txt"

if [ ! -f "$SHA_FILE" ]; then
  echo "Error: binaryen_commit.txt not found at $SHA_FILE"
  exit 1
fi

SHA=$(cat "$SHA_FILE" | tr -d '[:space:]')

# Detect Platform and Architecture
ARCH="$(uname -m)"

if [[ "$OSTYPE" == "linux-gnu"* ]]; then
  if [[ "$ARCH" == "x86_64" ]]; then
    PLATFORM="x86_64-linux"
  elif [[ "$ARCH" == "aarch64" || "$ARCH" == "arm64" ]]; then
    PLATFORM="aarch64-linux"
  else
    echo "Unsupported architecture for Linux: $ARCH"
    exit 1
  fi
elif [[ "$OSTYPE" == "darwin"* ]]; then
  if [[ "$ARCH" == "x86_64" ]]; then
    PLATFORM="x86_64-macos"
  elif [[ "$ARCH" == "arm64" ]]; then
    PLATFORM="arm64-macos"
  else
    echo "Unsupported architecture for macOS: $ARCH"
    exit 1
  fi
else
  echo "Unsupported OS: $OSTYPE"
  exit 1
fi

if [ -z "$1" ]; then
  echo "Error: Installation directory argument is required."
  echo "Usage: $0 <installation_directory>"
  exit 1
fi

OUT_DIR="$1"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"

# Check if the correct version is already installed (Cache check)
INSTALLED_SHA_FILE="$OUT_DIR/installed_sha.txt"
if [ -f "$INSTALLED_SHA_FILE" ]; then
  INSTALLED_SHA=$(cat "$INSTALLED_SHA_FILE" | tr -d '[:space:]')
  if [ "$INSTALLED_SHA" == "$SHA" ]; then
    echo "Binaryen SHA $SHA is already installed in $OUT_DIR for $PLATFORM. Skipping build."
    exit 0
  fi
fi

echo "Building Binaryen SHA $SHA for $PLATFORM..."

# Create a temporary directory for building
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

# Clone and checkout the specific commit
git clone https://chromium.googlesource.com/external/github.com/WebAssembly/binaryen/ "$TMP_DIR"
cd "$TMP_DIR"
git checkout "$SHA"

# Build Binaryen (Disabling tests saves significant time and dependencies)
mkdir build
cd build
cmake -G Ninja -DBUILD_TESTS=OFF -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$OUT_DIR" ..
ninja install

# Save the installed SHA to verify next time
echo "$SHA" > "$INSTALLED_SHA_FILE"

echo "Binaryen installed to $OUT_DIR/bin/"
echo "To use it, add to your PATH: export PATH=\"$OUT_DIR/bin:\$PATH\""
