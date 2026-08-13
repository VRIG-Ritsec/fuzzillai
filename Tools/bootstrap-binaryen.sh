#!/bin/bash
# Tools/bootstrap-binaryen.sh
set -e
set -o pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION_FILE="$REPO_ROOT/binaryen_version.txt"

if [ ! -f "$VERSION_FILE" ]; then
  echo "Error: binaryen_version.txt not found at $VERSION_FILE"
  exit 1
fi

VERSION=$(cat "$VERSION_FILE" | tr -d '[:space:]')

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

echo "Downloading Binaryen version $VERSION for $PLATFORM..."
URL="https://github.com/WebAssembly/binaryen/releases/download/version_${VERSION}/binaryen-version_${VERSION}-${PLATFORM}.tar.gz"

# Download and extract, stripping the top-level folder
curl -fsSL "$URL" | tar -xzf - -C "$OUT_DIR" --strip-components=1

echo "Binaryen installed to $OUT_DIR/bin/"
echo "To use it, add to your PATH: export PATH=\"$OUT_DIR/bin:\$PATH\""
