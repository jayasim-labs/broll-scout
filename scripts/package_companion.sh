#!/bin/bash
# Package broll-companion into a zip for editor distribution.
# Run from the project root: bash scripts/package_companion.sh
# Output: dist/broll-companion.zip

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DIST_DIR="$PROJECT_DIR/dist"

rm -rf "$DIST_DIR/broll-companion" "$DIST_DIR/broll-companion.zip"
mkdir -p "$DIST_DIR/broll-companion"

cp "$PROJECT_DIR/broll-companion/companion.py"            "$DIST_DIR/broll-companion/"
cp "$PROJECT_DIR/broll-companion/requirements.txt"        "$DIST_DIR/broll-companion/"
cp "$PROJECT_DIR/broll-companion/setup.bat"               "$DIST_DIR/broll-companion/"
cp "$PROJECT_DIR/broll-companion/setup.ps1"               "$DIST_DIR/broll-companion/"
cp "$PROJECT_DIR/broll-companion/start-companion.bat"     "$DIST_DIR/broll-companion/"
cp "$PROJECT_DIR/broll-companion/start-companion.ps1"     "$DIST_DIR/broll-companion/"
cp "$PROJECT_DIR/broll-companion/stop.bat"                "$DIST_DIR/broll-companion/"
cp "$PROJECT_DIR/broll-companion/update.bat"              "$DIST_DIR/broll-companion/"

# Convert bat/ps1 files to CRLF (cmd.exe silently crashes on LF-only)
for f in "$DIST_DIR/broll-companion"/*.bat "$DIST_DIR/broll-companion"/*.ps1; do
    [ -f "$f" ] && perl -pi -e 's/\r?\n/\r\n/' "$f"
done

cd "$DIST_DIR"
zip -r broll-companion.zip broll-companion/
rm -rf broll-companion/

echo ""
echo "Packaged: $DIST_DIR/broll-companion.zip"
echo "Upload this to GitHub Releases or share directly with editors."
