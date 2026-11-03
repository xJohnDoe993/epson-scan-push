# Epson ET-4800: "Scan to Computer" without the Windows software

A re-implementation of the Epson Event Manager as a small Python service (`scan-push.py`, standard library only).
The computer shows up on the printer under *Scan -> Computer*; selecting it starts the scan
(document feeder, or flatbed if the feeder is empty) and stores the result as a PDF.

Tested with the Epson ET-4800 on **Debian 13** (target: Paperless consume folder) and **Fedora 44** (target: folder).
Other Epson models that use the `epsonds` backend and have a "Computer" menu entry should work similarly,
but are untested. The IPs below (`10.0.0.x`) are placeholders.

## Protocol (reverse-engineered from a capture of the Windows software)
- **UDP 2968, SLPv2:** The printer sends a multicast request to `239.255.255.253` (AttrRqst,
  `service:NetScanMonitor-agent`, scope `PID 1185`) asking for receivers. We answer by unicast to printer:2968 with an
  AttrRply `(ClientName=..),(IPAddress=..),(EventPort=2968)` (XID copied from the request, sent 3 times, 0.5 s apart).
  Without a reply the printer shows "invalid".
- **TCP 2968:** The printer sends `POST /PushScan` (SOAP, `PushScanIDIn` = selected target). We reply `200 OK` with
  `StatusOut=OK`.
- **Scan:** Port 1865 on the printer speaks **TLS 1.2** (self-signed). SANE `epsonds` only supports plain text, so the
  service runs a plain-text -> TLS tunnel on `127.0.0.1:1865`; the scan device is `epsonds:net:127.0.0.1`.
- **Source selection:** Before scanning, the service asks the printer via eSCL (`/eSCL/ScannerStatus`) whether paper
  is in the feeder. The printer aborts the push session after about 20 s, so there are no long retries. Duplicate
  pushes during a scan are ignored.
- The flatbed is always scanned with `--batch-count=1`; otherwise `scanimage --batch` rescans the glass endlessly.
- Not solved: the printer's "Computer via USB" entry cannot be triggered from Linux.

## JPEG patch (important for speed)
`epsonds` requests RAW data from the printer (~49 MB per A4 page at 300 dpi). This clogs the printer's TLS link:
the carriage moves in jerks and one feeder page takes ~78 s. The printer can deliver JPEG (~0.3 MB).
The patch (inside `build-epsonds-jpeg.sh`) forces JPEG when the environment variable `EPSONDS_JPEG` is set (`scan-push` sets it).
Result: ~23 s per feeder page, ~28 s for a 300 dpi flatbed scan. The system SANE is otherwise left untouched.

## Quick install
Both installers install the required packages (`apt-get install` / `dnf install`), copy the scripts, set up the
systemd service and ask for the output folder, the name shown on the printer and the printer IP. All three can
also be passed as options (`-d`, `-n`, `-p`, `-y` = no questions) or changed in the variables at the top of the script.

```sh
# Debian / Ubuntu (as root)
sudo ./debian/install.sh -d /opt/paperless_data/consume -n PAPERLESS -p 10.0.0.119

# Fedora (as your normal user; sudo is called for dnf)
./fedora/install.sh -d ~/Documents/Scans -n Laptop -p 10.0.0.119
```

The output folder is passed to the service as `SCAN_DIR` (set in the systemd unit), so it can be changed later
by editing the unit and restarting the service.

## How the JPEG patch is installed
`build-epsonds-jpeg.sh` (used by both installers) downloads the SANE source matching your installed version
(1.3.1 on Debian 13, 1.4.0 on Fedora 44), applies the patch with `sed`, builds only the `epsonds` backend and
installs it into a separate directory: `/usr/local/lib/sane-jpeg` (root) or `~/.local/lib/sane-jpeg` (user).
The systemd unit points `LD_LIBRARY_PATH` there, so the system `libsane` is **not** modified and a package update
cannot overwrite the patch (after a SANE version update, run the script again). Newer SANE versions (1.4.0) need
a second patch where the PARA command is built; the script applies both. Skip the build with `-b`
(scans then take ~78 s per feeder page).

## Debian (Paperless) - `debian/`
`debian/install.sh` installs `python3 sane-utils img2pdf util-linux` plus the build packages
(`build-essential autoconf automake libtool gettext autopoint autoconf-archive pkg-config libjpeg-dev
libusb-1.0-0-dev curl`), builds the JPEG backend, installs `scan-push.py`, `scan2folder` and the unit.

Configuration via environment variables in the unit: `SCAN_DIR` (output folder), `EPSON_PRINTER`,
`CLIENT_NAME` (name on the printer, max. 15 characters), `SCAN_CMD`.

## Fedora (laptop) - `fedora/`
The same service as a systemd user unit; it only works while the laptop is on the home network.
`fedora/install.sh` installs `sane-backends sane-backends-drivers-scanners python3 python3-img2pdf util-linux curl`
plus the build packages (`autoconf automake libtool gettext-devel autoconf-archive libjpeg-turbo-devel
libusb1-devel gcc make`), builds the JPEG backend, installs the scripts into `~/.local/bin/` and enables the unit.

Firewall: the `FedoraWorkstation` zone allows 1025-65535 tcp/udp; port 2968 needs no opening.

## Troubleshooting
`journalctl -u scan-push` or `journalctl --user -u scan-push`. If the printer shows "invalid", the SLP reply is not
arriving (multicast on the network/VLAN, firewall on UDP 2968).
