#!/usr/bin/env bash
set -euo pipefail

peer_ip=""
duration="8"
ports=""
local_ports=""
remote_ports=""
default_local_ports="30001,30002,30003"
default_remote_ports="31001,31002,31003"

usage() {
  cat <<'EOF'
Usage: drop_neurotalk_udp.sh --peer-ip IP [--duration SECONDS]
                             [--ports 30001,30002,30003]
                             [--local-ports 30001,30002,30003]
                             [--remote-ports 31001,31002,31003]

Temporarily blocks NeuroTalk UDP traffic to and from one peer IP using iptables.
Defaults match the Linux configA-side asymmetric port layout.
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
    --local-ports)
      local_ports="${2:-}"
      shift 2
      ;;
    --remote-ports)
      remote_ports="${2:-}"
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

normalize_ports() {
  local raw="$1"
  local normalized=""
  IFS=',' read -r -a entries <<< "$raw"
  for entry in "${entries[@]}"; do
    local trimmed="${entry//[[:space:]]/}"
    [[ -z "$trimmed" ]] && continue
    if ! [[ "$trimmed" =~ ^[0-9]+$ ]]; then
      echo "Invalid port value: $trimmed" >&2
      exit 2
    fi
    if (( trimmed < 1 || trimmed > 65535 )); then
      echo "Port values must be between 1 and 65535: $trimmed" >&2
      exit 2
    fi
    if [[ -n "$normalized" ]]; then
      normalized+=","
    fi
    normalized+="$trimmed"
  done
  if [[ -z "$normalized" ]]; then
    echo "Provide at least one UDP port." >&2
    exit 2
  fi
  printf '%s\n' "$normalized"
}

if [[ -z "$local_ports" ]]; then
  if [[ -n "$ports" ]]; then
    local_ports="$ports"
  else
    local_ports="$default_local_ports"
  fi
fi
if [[ -z "$remote_ports" ]]; then
  if [[ -n "$ports" ]]; then
    remote_ports="$ports"
  else
    remote_ports="$default_remote_ports"
  fi
fi

local_ports="$(normalize_ports "$local_ports")"
remote_ports="$(normalize_ports "$remote_ports")"

cleanup() {
  iptables -w -D OUTPUT -p udp -d "$peer_ip" -m multiport --dports "$remote_ports" -j DROP 2>/dev/null || true
  iptables -w -D INPUT -p udp -s "$peer_ip" -m multiport --dports "$local_ports" -j DROP 2>/dev/null || true
}

trap cleanup EXIT

echo "Blocking NeuroTalk UDP traffic to $peer_ip for $duration second(s)."
echo "  Inbound local ports:  $local_ports"
echo "  Outbound remote ports: $remote_ports"
iptables -w -I OUTPUT -p udp -d "$peer_ip" -m multiport --dports "$remote_ports" -j DROP
iptables -w -I INPUT -p udp -s "$peer_ip" -m multiport --dports "$local_ports" -j DROP

sleep "$duration"
