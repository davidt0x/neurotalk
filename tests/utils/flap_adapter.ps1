[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AdapterName,

    [int]$DownSeconds = 5
)

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "This script must be run from an elevated PowerShell session."
}

if ($DownSeconds -le 0) {
    throw "DownSeconds must be positive."
}

$adapter = Get-NetAdapter -Name $AdapterName -ErrorAction Stop
Write-Host "Disabling adapter '$($adapter.Name)' for $DownSeconds second(s)."

Disable-NetAdapter -Name $adapter.Name -Confirm:$false
try {
    Start-Sleep -Seconds $DownSeconds
}
finally {
    Enable-NetAdapter -Name $adapter.Name -Confirm:$false
    Write-Host "Re-enabled adapter '$($adapter.Name)'."
}
