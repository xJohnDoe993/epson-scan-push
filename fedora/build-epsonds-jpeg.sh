#!/bin/bash
# Baut das SANE-Backend epsonds (1.4.0) mit JPEG-Patch und legt es nach ~/.local/lib/sane-jpeg/.
# Hintergrund: epsonds fordert vom ET-4800 RAW an (~49 MB je A4-Seite), das staut sich an der TLS-Strecke
# des Druckers. Mit gesetztem EPSONDS_JPEG verlangt das gepatchte Backend JPEG (siehe ../epsonds-jpeg.patch).
# Voraussetzung (einmalig, root): sudo dnf install autoconf automake libtool gettext-devel \
#   autoconf-archive libjpeg-turbo-devel libusb1-devel gcc make img2pdf
# Das System-Backend bleibt unveraendert; scan-push laedt das gepatchte per LD_LIBRARY_PATH.
set -euo pipefail
VER=1.4.0
DEST="${DEST:-$HOME/.local/lib/sane-jpeg}"
WORK="${WORK:-$(mktemp -d)}"
cd "$WORK"
curl -fsSL -o src.tar.gz "https://gitlab.com/sane-project/backends/-/archive/$VER/backends-$VER.tar.gz"
tar xzf src.tar.gz
cd "backends-$VER"
# Das Git-Archiv hat keine Versionsinfo (sonst "UNKNOWN" -> libtool-Fehler beim Linken)
echo "$VER" > .tarball-version
# Patch: bei EPSONDS_JPEG JPEG statt RAW
grep -q 'optindex > 0 && !s->hw->has_raw' backend/epsonds.c
sed -i 's/if (optindex > 0 \&\& !s->hw->has_raw) {/if (optindex > 0 \&\& (!s->hw->has_raw || getenv("EPSONDS_JPEG"))) {/' backend/epsonds.c
grep -q 'getenv("EPSONDS_JPEG")' backend/epsonds.c
# Zusaetzlich dort, wo der PARA-Befehl gebaut wird: scanimage 1.4.0 ruft den OPT_MODE-Handler nicht mehr
# in jedem Fall auf (Modus bleibt Standard), dann bliebe mode_jpeg 0 und der Drucker liefert RAW.
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
