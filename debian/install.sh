#!/bin/bash
# Installs scan-push on Debian/Ubuntu (run as root): packages, scripts, systemd unit.
# Usage: sudo ./install.sh [-d OUTPUT_DIR] [-n NAME_ON_PRINTER] [-p PRINTER_IP] [-y]
#   -d  folder for the finished PDFs (default: /opt/paperless_data/consume = Paperless consume folder)
#   -n  name under which this computer appears on the printer (max. 15 characters, default: hostname)
#   -p  printer IP (default: 10.0.0.119 - adjust this)
#   -y  no questions
set -euo pipefail

# --- default values, change them here --------------------------------------------------------
SCAN_DIR="${SCAN_DIR:-/opt/paperless_data/consume}"
CLIENT_NAME="${CLIENT_NAME:-$(hostname | tr a-z A-Z | cut -c1-15)}"
EPSON_PRINTER="${EPSON_PRINTER:-10.0.0.119}"
PACKAGES="python3 sane-utils img2pdf util-linux"
# ---------------------------------------------------------------------------------------------

ASK=1
while getopts "d:n:p:y" o; do
  case $o in
    d) SCAN_DIR=$OPTARG ;; n) CLIENT_NAME=$OPTARG ;; p) EPSON_PRINTER=$OPTARG ;; y) ASK=0 ;;
    *) sed -n '2,8p' "$0"; exit 1 ;;
  esac
done
[ "$(id -u)" -eq 0 ] || { echo "Please run as root (sudo)." >&2; exit 1; }
HERE=$(cd "$(dirname "$0")" && pwd)

if [ "$ASK" = 1 ] && [ -t 0 ]; then
  read -r -p "Output folder for PDFs [$SCAN_DIR]: " a; SCAN_DIR=${a:-$SCAN_DIR}
  read -r -p "Name on the printer [$CLIENT_NAME]: " a; CLIENT_NAME=${a:-$CLIENT_NAME}
  read -r -p "Printer IP [$EPSON_PRINTER]: " a; EPSON_PRINTER=${a:-$EPSON_PRINTER}
fi
CLIENT_NAME=${CLIENT_NAME:0:15}
case "$SCAN_DIR$CLIENT_NAME$EPSON_PRINTER" in *[\"\\]*|*$'\n'*) echo "Invalid characters in the input." >&2; exit 1 ;; esac

echo "== Installing packages: $PACKAGES"
apt-get update
# shellcheck disable=SC2086
apt-get install -y $PACKAGES

echo "== Installing files"
install -m 755 "$HERE/../scan-push.py" /usr/local/bin/scan-push.py
install -m 755 "$HERE/scan2folder" /usr/local/bin/scan2folder
mkdir -p "$SCAN_DIR"

echo "== systemd unit"
UNIT=/etc/systemd/system/scan-push.service
install -m 644 "$HERE/scan-push.service" "$UNIT"
sed -i "/^\[Service\]/a Environment=\"SCAN_DIR=$SCAN_DIR\"\nEnvironment=\"CLIENT_NAME=$CLIENT_NAME\"\nEnvironment=\"EPSON_PRINTER=$EPSON_PRINTER\"" "$UNIT"
systemctl daemon-reload
systemctl enable --now scan-push
systemctl restart scan-push

cat <<MSG

Done. Output folder: $SCAN_DIR, name on the printer: $CLIENT_NAME, printer: $EPSON_PRINTER
Status: systemctl status scan-push   |   Log: journalctl -u scan-push

IMPORTANT: Without the JPEG patch for libsane-epsonds a scan is very slow (~78 s per feeder page instead of ~23 s).
Patch and build instructions: README.md (Debian section), file ../epsonds-jpeg.patch.
The printer must be able to reach this machine (UDP/TCP 2968 inbound).
MSG
