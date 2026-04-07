[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PeerIp,

    [int]$DurationSeconds = 8,

    [int[]]$Ports = @(30001, 30002, 30003)
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

$portValues = @(
    $Ports |
        Sort-Object -Unique |
        ForEach-Object {
            if ($_ -lt 1 -or $_ -gt 65535) {
                throw "Port values must be between 1 and 65535."
            }
            [string]$_
        }
)
$portList = $portValues -join ","
$ruleToken = [guid]::NewGuid().ToString("N")
$displayPrefix = "NeuroTalk UDP Drop $ruleToken"
$createdRules = @()

function Add-DropRule {
    param(
        [string]$DisplayName,
        [string]$Direction,
        [string]$PortArgumentName,
        [string[]]$PortValues
    )

    $params = @{
        DisplayName   = $DisplayName
        Direction     = $Direction
        Action        = "Block"
        Protocol      = "UDP"
        RemoteAddress = $PeerIp
    }
    $params[$PortArgumentName] = $PortValues

    New-NetFirewallRule @params -ErrorAction Stop | Out-Null
    $script:createdRules += $DisplayName
}

try {
    Write-Host "Blocking NeuroTalk UDP traffic to $PeerIp on ports $portList for $DurationSeconds second(s)."

    Add-DropRule -DisplayName "$displayPrefix Outbound" -Direction "Outbound" -PortArgumentName "RemotePort" -PortValues $portValues
    Add-DropRule -DisplayName "$displayPrefix Inbound" -Direction "Inbound" -PortArgumentName "LocalPort" -PortValues $portValues

    Start-Sleep -Seconds $DurationSeconds
}
finally {
    foreach ($name in $createdRules) {
        Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
    }
    Write-Host "Removed temporary firewall rules."
}
