# Register vettercode as a nightly Windows Scheduled Task.
#
# Equivalent of the macOS launchd plist (com.pbulsink.vettercode.plist).
# Run from the repo root in a normal (non-elevated) PowerShell:
#
#     .\scripts\install-scheduled-task.ps1
#
# Remove it again with:
#
#     Unregister-ScheduledTask -TaskName vettercode -Confirm:$false
#
# Trigger it immediately with:
#
#     Start-ScheduledTask -TaskName vettercode

[CmdletBinding()]
param(
    # Time of day to run, 24h "HH:mm".
    [string]$At = "23:00",
    [string]$TaskName = "vettercode"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $repoRoot ".venv\Scripts\vettercode.exe"

if (-not (Test-Path $exe)) {
    throw "$exe not found. Run `uv sync` in $repoRoot first."
}

$action = New-ScheduledTaskAction -Execute $exe -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $At
# Laptops: allow the run on battery, and catch up if the machine was asleep.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Nightly vettercode GitHub issue agent" `
    -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName' (daily at $At) -> $exe"
