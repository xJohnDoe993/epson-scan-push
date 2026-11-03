#!/bin/bash
# Builds the SANE backend epsonds (1.4.0) with the JPEG patch and puts it into ~/.local/lib/sane-jpeg/.
# Background: epsonds requests RAW from the ET-4800 (~49 MB per A4 page), which clogs the printer's TLS link.
# With EPSONDS_JPEG set, the patched backend requests JPEG instead (see ../epsonds-jpeg.patch).
# Prerequisites (once, root): sudo dnf install autoconf automake libtool gettext-devel \
#   autoconf-archive libjpeg-turbo-devel libusb1-devel gcc make   (fedora/install.sh does this for you)
# The system backend stays untouched; scan-push loads the patched one via LD_LIBRARY_PATH.
set -euo pipefail
VER=1.4.0
DEST="${DEST:-$HOME/.local/lib/sane-jpeg}"
WORK="${WORK:-$(mktemp -d)}"
cd "$WORK"
curl -fsSL -o src.tar.gz "https://gitlab.com/sane-project/backends/-/archive/$VER/backends-$VER.tar.gz"
tar xzf src.tar.gz
cd "backends-$VER"
# The git archive has no version info (otherwise "UNKNOWN" -> libtool error while linking)
echo "$VER" > .tarball-version
# Patch: JPEG instead of RAW when EPSONDS_JPEG is set
grep -q 'optindex > 0 && !s->hw->has_raw' backend/epsonds.c
sed -i 's/if (optindex > 0 \&\& !s->hw->has_raw) {/if (optindex > 0 \&\& (!s->hw->has_raw || getenv("EPSONDS_JPEG"))) {/' backend/epsonds.c
grep -q 'getenv("EPSONDS_JPEG")' backend/epsonds.c
# Additionally where the PARA command is built: scanimage 1.4.0 no longer calls the OPT_MODE handler
# in every case (the mode stays at its default), so mode_jpeg would stay 0 and the printer would send RAW.
sed -i '/\/\* image transfer format \*\//i\	if (getenv("EPSONDS_JPEG") \&\& s->params.depth > 1)\n\t\ts->mode_jpeg = 1;\n' backend/epsonds.c
grep -B1 -A2 'getenv("EPSONDS_JPEG") && s->params.depth' backend/epsonds.c
autoreconf -fi >/dev/null
./configure --prefix=/usr --libdir=/usr/lib64 BACKENDS=epsonds --disable-locking >/dev/null
make -j"$(nproc)" -C lib >/dev/null
make -j"$(nproc)" -C sanei >/dev/null
make -j"$(nproc)" -C backend libsane-epsonds.la >/dev/null
mkdir -p "$DEST"
cp backend/.libs/libsane-epsonds.so "$DEST/libsane-epsonds.so.1"
echo "OK: $DEST/libsane-epsonds.so.1"
