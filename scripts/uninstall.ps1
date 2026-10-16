<#
.SYNOPSIS
  Reverses install.ps1: removes only what it added. The learned store (your data) is kept.
.PARAMETER DryRun
  Print the planned changes and modify nothing.
.PARAMETER Python
  Python interpreter that has the `learned` package (default: `python` on PATH).
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [string]$Python
)
$ErrorActionPreference = 'Stop'

$UserHome = $env:USERPROFILE
if (-not $UserHome) { throw 'USERPROFILE is not set' }
$Claude = Join-Path $UserHome '.claude'
$Settings = Join-Path $Claude 'settings.json'
$ClaudeMd = Join-Path $Claude 'CLAUDE.md'
$SkillDst = Join-Path $Claude 'skills\recall'
$CmdDst = Join-Path $Claude 'commands\learned.md'
$TaskName = 'LearnedMemoryConsolidate'

if (-not $Python) { $Python = (Get-Command python -ErrorAction Stop).Source }
$Python = (& $Python -c 'import sys; print(sys.executable)').Trim()
if ($LASTEXITCODE -ne 0 -or -not $Python) { throw 'could not resolve the Python interpreter' }

function Invoke-Util {
    param([string[]]$UtilArgs)
    & $Python -m learned.install_util @UtilArgs
    if ($LASTEXITCODE -ne 0) { throw "install_util failed: $($UtilArgs -join ' ')" }
}

$mode = if ($DryRun) { 'DRY RUN - nothing will be changed' } else { 'UNINSTALL' }
Write-Output "learned-memory uninstall ($mode)"
Write-Output "home: $UserHome"
Write-Output ''

# 6. store: data is never deleted
Write-Output '[6/6] Store: left in place (your learned data is not deleted)'

# 5. CLAUDE.md rule
Write-Output "[5/6] CLAUDE.md: remove the learned-memory block from $ClaudeMd"
if ($DryRun) { Invoke-Util @('rule', 'remove', '--claude-md', $ClaudeMd) }
else { Invoke-Util @('rule', 'remove', '--claude-md', $ClaudeMd, '--write') }

# 4. skill + command
Write-Output "[4/6] skill and command: remove $SkillDst and $CmdDst"
if (-not $DryRun) {
    if (Test-Path $SkillDst) { Remove-Item -Recurse -Force $SkillDst }
    if (Test-Path $CmdDst) { Remove-Item -Force $CmdDst }
}

# 3. scheduled task
Write-Output "[3/6] scheduled task: Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
if (-not $DryRun) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
}

# 2. hooks
Write-Output "[2/6] settings.json: remove the learned SessionEnd/SessionStart hooks from $Settings"
if ($DryRun) { Invoke-Util @('unmerge-hooks', '--settings', $Settings) }
else { Invoke-Util @('unmerge-hooks', '--settings', $Settings, '--write') }

# 1. package (last: the steps above need it)
Write-Output "[1/6] pip uninstall: $Python -m pip uninstall -y learned"
if (-not $DryRun) {
    & $Python -m pip uninstall -y learned
}

Write-Output ''
if ($DryRun) { Write-Output 'Dry run complete. Nothing was changed.' }
else { Write-Output 'Uninstalled. Restart open Claude Code sessions.' }
