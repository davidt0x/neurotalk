#!/usr/bin/env bash
set -euo pipefail

iface=""
duration="20"
loss="20%"
delay_ms="200"
jitter_ms="50"

usage() {
  cat <<'EOF'
Usage: netem_neurotalk.sh --iface IFACE [--duration SECONDS] [--loss 20%] [--delay-ms 200] [--jitter-ms 50]

Applies a temporary netem qdisc to simulate a lossy or jittery link.
This affects all traffic on the interface while active.
Must be run as root.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --iface)
      iface="${2:-}"
      shift 2
      ;;
    --duration)
      duration="${2:-}"
      shift 2
      ;;
    --loss)
      loss="${2:-}"
      shift 2
      ;;
    --delay-ms)
      delay_ms="${2:-}"
      shift 2
      ;;
    --jitter-ms)
      jitter_ms="${2:-}"
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

if ! command -v tc >/dev/null 2>&1; then
  echo "tc is required for this script" >&2
  exit 1
fi

cleanup() {
  tc qdisc del dev "$iface" root 2>/dev/null || true
}

trap cleanup EXIT

echo "Applying netem on $iface for $duration second(s): loss=$loss delay=${delay_ms}ms jitter=${jitter_ms}ms"
tc qdisc replace dev "$iface" root netem loss "$loss" delay "${delay_ms}ms" "${jitter_ms}ms"
sleep "$duration"
