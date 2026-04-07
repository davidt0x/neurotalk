#!/usr/bin/env bash
set -euo pipefail

iface=""
down_seconds="5"

usage() {
  cat <<'EOF'
Usage: flap_interface.sh --iface IFACE [--down-seconds SECONDS]

Brings a Linux network interface down and then back up.
Must be run as root.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --iface)
      iface="${2:-}"
      shift 2
      ;;
    --down-seconds)
      down_seconds="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$iface" ]]; then
  echo "--iface is required" >&2
  usage >&2
  exit 2
fi

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run this script with sudo or as root." >&2
  exit 1
fi

if ! command -v ip >/dev/null 2>&1; then
  echo "ip is required for this script" >&2
  exit 1
fi

echo "Bringing interface $iface down for $down_seconds second(s)."
ip link set dev "$iface" down
trap 'ip link set dev "$iface" up 2>/dev/null || true' EXIT
sleep "$down_seconds"
ip link set dev "$iface" up
trap - EXIT
echo "Interface $iface restored."
