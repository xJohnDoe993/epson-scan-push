#!/bin/bash
# Builds the SANE backend epsonds with the JPEG patch (matching the installed SANE version) and installs it
# into a separate directory; the system backend stays untouched and survives package updates.
#   as root:      /usr/local/lib/sane-jpeg   (Debian, system service)
#   as a user:    ~/.local/lib/sane-jpeg     (Fedora, user service)
# scan-push loads it through LD_LIBRARY_PATH (set in the systemd unit by the install scripts).
#
# Why: epsonds requests RAW from the ET-4800 (~49 MB per A4 page), which clogs the printer's TLS link
# (carriage moves in jerks, ~78 s per feeder page). With EPSONDS_JPEG set, the patched backend requests JPEG
# instead (~0.3 MB, ~23 s per page).
#
# Build dependencies (the install scripts install them):
#   Debian: build-essential autoconf automake libtool gettext autopoint autoconf-archive pkg-config
#           libjpeg-dev libusb-1.0-0-dev curl
#   Fedora: autoconf automake libtool gettext-devel autoconf-archive libjpeg-turbo-devel libusb1-devel gcc make curl
# Optional environment: VER (SANE version, default: installed one), DEST (install dir), WORK (build dir)
set -euo pipefail
VER="${VER:-$(scanimage -V 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)}"
[ -n "$VER" ] || { echo "Could not detect the SANE version (is scanimage installed?). Set VER=x.y.z." >&2; exit 1; }
if [ "$(id -u)" -eq 0 ]; then DEST="${DEST:-/usr/local/lib/sane-jpeg}"; else DEST="${DEST:-$HOME/.local/lib/sane-jpeg}"; fi
WORK="${WORK:-$(mktemp -d)}"
echo "== Building epsonds $VER with JPEG patch in $WORK"
cd "$WORK"
curl -fsSL -o src.tar.gz "https://gitlab.com/sane-project/backends/-/archive/$VER/backends-$VER.tar.gz"
tar xzf src.tar.gz
cd "backends-$VER"
# The git archive has no version info (otherwise "UNKNOWN" -> libtool error while linking)
echo "$VER" > .tarball-version

# Patch 1: JPEG instead of RAW when EPSONDS_JPEG is set (OPT_MODE handler)
grep -q 'optindex > 0 && !s->hw->has_raw' backend/epsonds.c
sed -i 's/if (optindex > 0 \&\& !s->hw->has_raw) {/if (optindex > 0 \&\& (!s->hw->has_raw || getenv("EPSONDS_JPEG"))) {/' backend/epsonds.c
grep -q 'getenv("EPSONDS_JPEG")' backend/epsonds.c
# Patch 2: the same where the PARA command is built. Newer scanimage versions (1.4.0) do not call the
# OPT_MODE handler in every case (the mode stays at its default), so mode_jpeg would stay 0 and the printer
# would send RAW.
grep -q '/\* image transfer format \*/' backend/epsonds.c
sed -i '/\/\* image transfer format \*\//i\	if (getenv("EPSONDS_JPEG") \&\& s->params.depth > 1)\n\t\ts->mode_jpeg = 1;\n' backend/epsonds.c
grep -q 'getenv("EPSONDS_JPEG") && s->params.depth' backend/epsonds.c

autoreconf -fi >/dev/null
./configure --prefix=/usr BACKENDS=epsonds --disable-locking >/dev/null
make -j"$(nproc)" -C lib >/dev/null
make -j"$(nproc)" -C sanei >/dev/null
make -j"$(nproc)" -C backend libsane-epsonds.la >/dev/null
mkdir -p "$DEST"
cp backend/.libs/libsane-epsonds.so "$DEST/libsane-epsonds.so.1"
echo "OK: $DEST/libsane-epsonds.so.1"
