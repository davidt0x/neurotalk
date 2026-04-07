#!/usr/bin/env bash
set -euo pipefail

peer_ip=""
duration="8"
ports="30001,30002,30003"

usage() {
  cat <<'EOF'
Usage: drop_neurotalk_udp.sh --peer-ip IP [--duration SECONDS] [--ports 30001,30002,30003]

Temporarily blocks NeuroTalk UDP traffic to and from one peer IP using iptables.
Must be run as root.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --peer-ip)
      peer_ip="${2:-}"
      shift 2
      ;;
    --duration)
      duration="${2:-}"
      shift 2
      ;;
    --ports)
      ports="${2:-}"
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

if [[ -z "$peer_ip" ]]; then
  echo "--peer-ip is required" >&2
  usage >&2
  exit 2
fi

if ! command -v iptables >/dev/null 2>&1; then
  echo "iptables is required for this script" >&2
  exit 1
fi

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run this script with sudo or as root." >&2
  exit 1
fi

cleanup() {
  iptables -w -D OUTPUT -p udp -d "$peer_ip" -m multiport --dports "$ports" -j DROP 2>/dev/null || true
  iptables -w -D INPUT -p udp -s "$peer_ip" -m multiport --dports "$ports" -j DROP 2>/dev/null || true
}

trap cleanup EXIT

echo "Blocking NeuroTalk UDP traffic to $peer_ip on ports $ports for $duration second(s)."
iptables -w -I OUTPUT -p udp -d "$peer_ip" -m multiport --dports "$ports" -j DROP
iptables -w -I INPUT -p udp -s "$peer_ip" -m multiport --dports "$ports" -j DROP

sleep "$duration"
