# Network Fault Injection Helpers

These scripts are for manually simulating transient NeuroTalk connectivity
problems between two hosts during local testing.

They are intentionally stored under `tests/utils/` because they support manual
test workflows, but they are not part of the automated pytest suite.

## Windows PowerShell

### `drop_neurotalk_udp.ps1`

Temporarily blocks NeuroTalk UDP traffic to and from one peer IP.

The default port behavior matches the Windows `configB.yaml` side of the home
test setup:

- inbound local ports: `31001,31002,31003`
- outbound remote ports: `30001,30002,30003`

Example:

```powershell
.\drop_neurotalk_udp.ps1 -PeerIp 192.168.1.155 -DurationSeconds 8
```

Override the defaults explicitly:

```powershell
.\drop_neurotalk_udp.ps1 -PeerIp 192.168.1.155 `
  -LocalPorts 31001,31002,31003 `
  -RemotePorts 30001,30002,30003 `
  -DurationSeconds 8
```

Control-only outage:

```powershell
.\drop_neurotalk_udp.ps1 -PeerIp 192.168.1.151 -Ports 30003 -DurationSeconds 8
```

### `flap_adapter.ps1`

Disables and then re-enables a Windows network adapter.

Example:

```powershell
.\flap_adapter.ps1 -AdapterName "Wi-Fi" -DownSeconds 5
```

Both PowerShell scripts require an elevated PowerShell session.

## Linux

### `drop_neurotalk_udp.sh`

Temporarily blocks NeuroTalk UDP traffic to and from one peer IP using
`iptables`.

The default port behavior matches the Linux `configA.yaml` side of the home test
setup:

- inbound local ports: `30001,30002,30003`
- outbound remote ports: `31001,31002,31003`

Example:

```bash
sudo ./drop_neurotalk_udp.sh --peer-ip 192.168.1.151 --duration 8
```

Override the defaults explicitly:

```bash
sudo ./drop_neurotalk_udp.sh \
  --peer-ip 192.168.1.151 \
  --local-ports 30001,30002,30003 \
  --remote-ports 31001,31002,31003 \
  --duration 8
```

### `flap_interface.sh`

Brings a Linux network interface down and then back up.

Example:

```bash
sudo ./flap_interface.sh --iface wlan0 --down-seconds 5
```

### `netem_neurotalk.sh`

Applies Linux `tc netem` impairment to an interface to simulate lossy or jittery
Wi-Fi. This affects all traffic on the chosen interface while active.

Example:

```bash
sudo ./netem_neurotalk.sh --iface wlan0 --duration 20 --loss 20% --delay-ms 200 --jitter-ms 50
```

## Notes

- `drop_neurotalk_udp.*` is the cleanest way to test reconnect logic because it
  isolates the NeuroTalk UDP ports instead of taking the whole machine offline.
- For asymmetric configs, use separate local and remote port lists. Inbound
  rules must target this machine's local ports, while outbound rules must target
  the peer's local ports.
- Adapter flap scripts are harsher and closer to a NIC reset or driver issue.
- If you run multiple experiments back-to-back, verify firewall and qdisc rules
  were cleaned up before starting the next run.
