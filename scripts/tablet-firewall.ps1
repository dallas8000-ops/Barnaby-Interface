# Let a paired tablet reach Barnaby on your home network, and nowhere else.
#
#   Run once, as administrator (right-click PowerShell > Run as administrator):
#     powershell -ExecutionPolicy Bypass -File .\scripts\tablet-firewall.ps1
#   Different port (match Settings > Tablet access):   ... -Port 9100
#   Remove the rule:                                   ... -Remove
#
# The rule allows inbound TCP on the tablet port for the *Private* network profile only,
# so on a network Windows treats as Public (a cafe, a hotel) nothing can connect even if
# tablet access is left on. Barnaby also refuses to start tablet access on a Public network.
param(
    [ValidateRange(1024, 65535)][int]$Port = 8766,
    [switch]$Remove
)
$ErrorActionPreference = 'Stop'
$name = 'Barnaby tablet access'

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Host "Run this from PowerShell opened with 'Run as administrator'." -ForegroundColor Yellow
    exit 1
}

Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
if ($Remove) {
    Write-Host "Removed the '$name' firewall rule." -ForegroundColor Green
    exit 0
}

New-NetFirewallRule -DisplayName $name -Description 'Paired tablets reach Barnaby over HTTPS on the home network.' `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -Profile Private | Out-Null
Write-Host "Allowed TCP $Port on Private networks only ('$name')." -ForegroundColor Green

$public = Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq 'Public' }
foreach ($p in $public) {
    Write-Host "Note: '$($p.Name)' is set to Public, so the tablet can't connect over it." -ForegroundColor Yellow
    Write-Host "      If it's your home network: Settings > Network & internet > $($p.InterfaceAlias) > Private network."
}
