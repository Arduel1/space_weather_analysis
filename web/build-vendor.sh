#!/bin/sh
# Writes the third-party files of the web frontend into vendor/; run `npm ci --ignore-scripts` in web/ first.
# npm ci has checked every package file against the sha512 hash of package-lock.json; index.html loads the files by
# these paths.
set -eu

# Directory of this script, the web frontend
WEB_DIR=$(cd "$(dirname "$0")" && pwd)
MODULES_DIR="$WEB_DIR/node_modules"
VENDOR_DIR="$WEB_DIR/vendor"

# Natural Earth coastlines at 1:110 million, raw file of the pinned release tag
NATURAL_EARTH_VERSION=5.1.2
NATURAL_EARTH_FILE=ne_110m_coastline.geojson
NATURAL_EARTH_URL="https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v$NATURAL_EARTH_VERSION/geojson/$NATURAL_EARTH_FILE"
# SHA-256 of that file, checked before it is written into vendor/
NATURAL_EARTH_SHA256=851f581ff5ffb844deed8ae1a9ce22e3c4bb3d74fa342cadb5d8e39b41ae7c3c

# Files of the three package as `{source in the package} {target below vendor/three}`
THREE_FILES="
LICENSE LICENSE
build/three.module.js three.module.js
build/three.core.js three.core.js
examples/jsm/controls/OrbitControls.js addons/controls/OrbitControls.js
examples/jsm/lines/LineMaterial.js addons/lines/LineMaterial.js
examples/jsm/lines/LineSegments2.js addons/lines/LineSegments2.js
examples/jsm/lines/LineSegmentsGeometry.js addons/lines/LineSegmentsGeometry.js
examples/jsm/renderers/CSS2DRenderer.js addons/renderers/CSS2DRenderer.js
"

fail() {
    echo "build-vendor.sh: $1" >&2
    exit 1
}

# Copies one file and creates the directory of the target; a missing source stops the build.
copy_file() {
    [ -f "$1" ] || fail "missing $1, run \`npm ci --ignore-scripts\` in $WEB_DIR first"
    mkdir -p "$(dirname "$2")"
    cp "$1" "$2"
}

# Downloads a URL to a file with curl or, where curl is missing (Alpine images), with wget.
download() {
    if command -v curl >/dev/null 2>&1; then
        curl --fail --silent --show-error --location --output "$2" "$1"
    else
        wget -q -O "$2" "$1"
    fi
}

[ -d "$MODULES_DIR" ] || fail "missing $MODULES_DIR, run \`npm ci --ignore-scripts\` in $WEB_DIR first"

echo "$THREE_FILES" | while read -r source target; do
    [ -n "$source" ] || continue
    copy_file "$MODULES_DIR/three/$source" "$VENDOR_DIR/three/$target"
done

copy_file "$MODULES_DIR/plotly.js-dist-min/plotly.min.js" "$VENDOR_DIR/plotly.min.js"
copy_file "$MODULES_DIR/plotly.js-dist-min/LICENSE" "$VENDOR_DIR/plotly.LICENSE"

# The download goes to a temporary file first, so a failed or altered download never replaces the file in vendor/
mkdir -p "$VENDOR_DIR/naturalearth"
download_file=$(mktemp)
trap 'rm -f "$download_file"' EXIT
download "$NATURAL_EARTH_URL" "$download_file" || fail "download of $NATURAL_EARTH_URL failed"
echo "$NATURAL_EARTH_SHA256  $download_file" | sha256sum -c - >/dev/null 2>&1 \
    || fail "SHA-256 of $NATURAL_EARTH_URL differs from $NATURAL_EARTH_SHA256"
cp "$download_file" "$VENDOR_DIR/naturalearth/$NATURAL_EARTH_FILE"

cat > "$VENDOR_DIR/naturalearth/LICENSE" <<EOF
Natural Earth $NATURAL_EARTH_VERSION, $NATURAL_EARTH_FILE, from https://github.com/nvkelso/natural-earth-vector
All Natural Earth map data are in the public domain: https://www.naturalearthdata.com/about/terms-of-use/
EOF

echo "build-vendor.sh: third-party files written to $VENDOR_DIR"
