[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PeerIp,

    [int]$DurationSeconds = 8,

    [int[]]$Ports = @(30001, 30002, 30003),

    [string]$RuleGroup = "NeuroTalkTest"
)

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "This script must be run from an elevated PowerShell session."
}

if ($DurationSeconds -le 0) {
    throw "DurationSeconds must be positive."
}

if ($Ports.Count -eq 0) {
    throw "Provide at least one UDP port."
}

$portList = ($Ports | Sort-Object -Unique) -join ","
$displayPrefix = "NeuroTalk UDP Drop $PID"
$createdRules = @()

function Add-DropRule {
    param(
        [string]$DisplayName,
        [string]$Direction,
        [string]$PortArgumentName,
        [string]$PortList
    )

    $params = @{
        DisplayName   = $DisplayName
        DisplayGroup  = $RuleGroup
        Direction     = $Direction
        Action        = "Block"
        Protocol      = "UDP"
        RemoteAddress = $PeerIp
    }
    $params[$PortArgumentName] = $PortList

    New-NetFirewallRule @params | Out-Null
    $script:createdRules += $DisplayName
}

try {
    Write-Host "Blocking NeuroTalk UDP traffic to $PeerIp on ports $portList for $DurationSeconds second(s)."

    Add-DropRule -DisplayName "$displayPrefix Outbound" -Direction "Outbound" -PortArgumentName "RemotePort" -PortList $portList
    Add-DropRule -DisplayName "$displayPrefix Inbound" -Direction "Inbound" -PortArgumentName "LocalPort" -PortList $portList

    Start-Sleep -Seconds $DurationSeconds
}
finally {
    foreach ($name in $createdRules) {
        Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
    }
    Write-Host "Removed temporary firewall rules."
}
