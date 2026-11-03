#!/bin/bash
# Installs scan-push for Fedora as a systemd user service (run as your normal user, not root).
# Usage: ./install.sh [-d OUTPUT_DIR] [-n NAME_ON_PRINTER] [-p PRINTER_IP] [-b] [-y]
#   -d  folder for the finished PDFs (default: ~/Documents/Scans, localized via xdg-user-dir)
#   -n  name under which this computer appears on the printer (max. 15 characters, default: hostname)
#   -p  printer IP (default: 10.0.0.119 - adjust this)
#   -b  skip building the JPEG-patched epsonds backend (scans will be very slow)
#   -y  no questions
set -euo pipefail

# --- default values, change them here --------------------------------------------------------
SCAN_DIR="${SCAN_DIR:-$(xdg-user-dir DOCUMENTS 2>/dev/null || echo "$HOME/Documents")/Scans}"
CLIENT_NAME="${CLIENT_NAME:-$(hostname -s | cut -c1-15)}"
EPSON_PRINTER="${EPSON_PRINTER:-10.0.0.119}"
RUN_PACKAGES="sane-backends sane-backends-drivers-scanners python3 python3-img2pdf util-linux curl"
BUILD_PACKAGES="autoconf automake libtool gettext-devel autoconf-archive libjpeg-turbo-devel libusb1-devel gcc make"
# ---------------------------------------------------------------------------------------------

ASK=1; BUILD=1
while getopts "d:n:p:by" o; do
  case $o in
    d) SCAN_DIR=$OPTARG ;; n) CLIENT_NAME=$OPTARG ;; p) EPSON_PRINTER=$OPTARG ;;
    b) BUILD=0 ;; y) ASK=0 ;;
    *) sed -n '2,9p' "$0"; exit 1 ;;
  esac
done
[ "$(id -u)" -ne 0 ] || { echo "Run as your normal user (sudo is called where needed)." >&2; exit 1; }
HERE=$(cd "$(dirname "$0")" && pwd)

if [ "$ASK" = 1 ] && [ -t 0 ]; then
  read -r -p "Output folder for PDFs [$SCAN_DIR]: " a; SCAN_DIR=${a:-$SCAN_DIR}
  read -r -p "Name on the printer [$CLIENT_NAME]: " a; CLIENT_NAME=${a:-$CLIENT_NAME}
  read -r -p "Printer IP [$EPSON_PRINTER]: " a; EPSON_PRINTER=${a:-$EPSON_PRINTER}
fi
CLIENT_NAME=${CLIENT_NAME:0:15}
case "$SCAN_DIR$CLIENT_NAME$EPSON_PRINTER" in *[\"\\]*|*$'\n'*) echo "Invalid characters in the input." >&2; exit 1 ;; esac

PKGS="$RUN_PACKAGES"; [ "$BUILD" = 1 ] && PKGS="$PKGS $BUILD_PACKAGES"
echo "== Installing packages: $PKGS"
# shellcheck disable=SC2086
sudo dnf install -y $PKGS

if [ "$BUILD" = 1 ]; then
  echo "== Building the JPEG-patched epsonds backend (downloads SANE 1.4.0 source and compiles it)"
  "$HERE/build-epsonds-jpeg.sh"
fi

echo "== Installing files"
install -Dm755 "$HERE/../scan-push.py" "$HOME/.local/bin/scan-push.py"
install -Dm755 "$HERE/scan2folder" "$HOME/.local/bin/scan2folder"
mkdir -p "$SCAN_DIR"

echo "== systemd user unit"
UNIT="$HOME/.config/systemd/user/scan-push.service"
install -Dm644 "$HERE/scan-push.service" "$UNIT"
sed -i "/^\[Service\]/a Environment=\"SCAN_DIR=$SCAN_DIR\"\nEnvironment=\"EPSON_PRINTER=$EPSON_PRINTER\"" "$UNIT"
sed -i "s|^Environment=CLIENT_NAME=.*|Environment=\"CLIENT_NAME=$CLIENT_NAME\"|" "$UNIT"
systemctl --user daemon-reload
systemctl --user enable --now scan-push
systemctl --user restart scan-push

cat <<MSG

Done. Output folder: $SCAN_DIR, name on the printer: $CLIENT_NAME, printer: $EPSON_PRINTER
Status: systemctl --user status scan-push   |   Log: journalctl --user -u scan-push
The service only runs while you are logged in; for always-on use: loginctl enable-linger \$USER
Firewall: the FedoraWorkstation zone allows 1025-65535; port 2968 needs no opening.
MSG
