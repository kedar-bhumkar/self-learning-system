<#
.SYNOPSIS
  Installs learned memory for the current user: package, hooks, daily task, skill, command, rule.
.PARAMETER DryRun
  Print the exact planned changes and modify nothing.
.PARAMETER Python
  Python interpreter to register (default: the `python` on PATH).
.PARAMETER LearnedHome
  Store location (default: <USERPROFILE>\.claude\learned, or $env:LEARNED_HOME when set).
  A non-default location needs -AcceptCustomHome.
.PARAMETER AcceptCustomHome
  Required to continue when the store location is not the default: hooks and the scheduled
  task cannot see -LearnedHome, only a persisted user environment variable (setx LEARNED_HOME).
.NOTES
  Everything lives under $env:USERPROFILE\.claude (never a hard-coded home).
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [string]$Python,
    [string]$LearnedHome,
    [switch]$AcceptCustomHome
)
$ErrorActionPreference = 'Stop'

$Repo = Split-Path -Parent $PSScriptRoot
$UserHome = $env:USERPROFILE
if (-not $UserHome) { throw 'USERPROFILE is not set' }
$Claude = Join-Path $UserHome '.claude'
$Settings = Join-Path $Claude 'settings.json'
$ClaudeMd = Join-Path $Claude 'CLAUDE.md'
$SkillDst = Join-Path $Claude 'skills\recall'
$CmdDst = Join-Path $Claude 'commands\learned.md'
$DefaultHome = Join-Path $Claude 'learned'
if (-not $LearnedHome) { $LearnedHome = if ($env:LEARNED_HOME) { $env:LEARNED_HOME } else { $DefaultHome } }
$TaskName = 'LearnedMemoryConsolidate'

if (-not $Python) { $Python = (Get-Command python -ErrorAction Stop).Source }
$Python = (& $Python -c 'import sys; print(sys.executable)').Trim()
if ($LASTEXITCODE -ne 0 -or -not $Python) { throw 'could not resolve the Python interpreter' }

function Test-Managed {
    # True when the file carries our ownership marker.
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    return [bool](Select-String -LiteralPath $Path -SimpleMatch 'learned-memory: managed' -Quiet)
}

function Invoke-Util {
    param([string[]]$UtilArgs)
    & $Python -P -m learned.install_util @UtilArgs
    if ($LASTEXITCODE -ne 0) { throw "install_util failed: $($UtilArgs -join ' ')" }
}

function Format-InstallFailure {
    # Multi-line failure report: what finished, what failed, how to undo.
    param([string[]]$Completed, [string]$Failed, [string]$Reason)
    $done = if ($Completed -and $Completed.Count -gt 0) { $Completed -join ', ' } else { '(none)' }
    return @(
        "completed steps: $done",
        "failed step: $Failed ($Reason)",
        'manual undo: run scripts\uninstall.ps1'
    ) -join [Environment]::NewLine
}

function Test-CustomHome {
    param([string]$Path, [string]$Default)
    $a = $Path.Replace('/', '\').TrimEnd('\').ToLowerInvariant()
    $b = $Default.Replace('/', '\').TrimEnd('\').ToLowerInvariant()
    return $a -ne $b
}

$mode = if ($DryRun) { 'DRY RUN - nothing will be changed' } else { 'INSTALL' }
Write-Output "learned-memory install ($mode)"
Write-Output "python: $Python"
Write-Output "repo:   $Repo"
Write-Output "home:   $UserHome"
Write-Output ''

# preflight: a custom store location is invisible to hooks and the scheduled task
if (Test-CustomHome $LearnedHome $DefaultHome) {
    Write-Output "WARNING: store location '$LearnedHome' differs from the default '$DefaultHome'."
    Write-Output '         Hooks and the scheduled task cannot see it unless LEARNED_HOME is persisted'
    Write-Output "         as a user environment variable: setx LEARNED_HOME `"$LearnedHome`""
    if (-not $AcceptCustomHome) {
        Write-Output 'Nothing was changed. Re-run with -AcceptCustomHome to continue anyway.'
        exit 1
    }
    Write-Output '         Continuing because -AcceptCustomHome was given.'
}

# preflight: never overwrite a skill or command the user wrote themselves
$refused = @()
if ((Test-Path -LiteralPath $SkillDst) -and -not (Test-Managed (Join-Path $SkillDst 'SKILL.md'))) {
    $refused += "REFUSED: $SkillDst exists and is not managed by learned-memory (no 'learned-memory: managed' marker in SKILL.md)"
}
if ((Test-Path -LiteralPath $CmdDst) -and -not (Test-Managed $CmdDst)) {
    $refused += "REFUSED: $CmdDst exists and is not managed by learned-memory (no 'learned-memory: managed' marker)"
}
if ($refused.Count -gt 0) {
    $refused | ForEach-Object { Write-Output $_ }
    Write-Output 'Nothing was changed. Move or rename the existing item(s), then run again.'
    exit 1
}

$completed = @()
$current = '1/6 package'
try {
# 1. package
Write-Output "[1/6] pip install -e: $Python -m pip install -e `"$Repo`""
if (-not $DryRun) {
    & $Python -m pip install -e $Repo
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }
}

$completed += '1/6 package'; $current = '2/6 hooks'
# 2. hooks
Write-Output "[2/6] settings.json hooks (SessionEnd timeout 5, SessionStart startup|resume|clear): $Settings"
if ($DryRun) {
    Invoke-Util @('merge-hooks', '--settings', $Settings, '--python', $Python)
    Write-Output '      (a timestamped backup of settings.json is written before the real run writes it)'
} else {
    Invoke-Util @('merge-hooks', '--settings', $Settings, '--python', $Python, '--write')
}

$completed += '2/6 hooks'; $current = '3/6 scheduled task'
# 3. scheduled task
$taskCmd = "Register-ScheduledTask -TaskName $TaskName -Force " +
    "-Action (New-ScheduledTaskAction -Execute `"$Python`" -Argument `"-P -m learned.consolidate`") " +
    "-Trigger (New-ScheduledTaskTrigger -Daily -At 03:00) " +
    "-Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable)"
Write-Output "[3/6] scheduled task $TaskName, daily 03:00, StartWhenAvailable, running: `"$Python`" -P -m learned.consolidate"
Write-Output "      $taskCmd"
if (-not $DryRun) {
    $action = New-ScheduledTaskAction -Execute $Python -Argument '-P -m learned.consolidate'
    $trigger = New-ScheduledTaskTrigger -Daily -At 03:00
    $tset = New-ScheduledTaskSettingsSet -StartWhenAvailable
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $tset -Force | Out-Null
}

$completed += '3/6 scheduled task'; $current = '4/6 skill and command'
# 4. skill + command
Write-Output "[4/6] skill and command copy: $Repo\skills\recall -> $SkillDst ; $Repo\commands\learned.md -> $CmdDst"
if (-not $DryRun) {
    New-Item -ItemType Directory -Force -Path (Split-Path $SkillDst), (Split-Path $CmdDst) | Out-Null
    if (Test-Path $SkillDst) { Remove-Item -Recurse -Force $SkillDst }
    Copy-Item -Recurse -Force (Join-Path $Repo 'skills\recall') $SkillDst
    Copy-Item -Force (Join-Path $Repo 'commands\learned.md') $CmdDst
}

$completed += '4/6 skill and command'; $current = '5/6 CLAUDE.md rule'
# 5. CLAUDE.md rule
Write-Output "[5/6] CLAUDE.md recall rule (between learned-memory:start/end markers): $ClaudeMd"
if ($DryRun) {
    Invoke-Util @('rule', 'add', '--claude-md', $ClaudeMd)
} else {
    Invoke-Util @('rule', 'add', '--claude-md', $ClaudeMd, '--write')
}

$completed += '5/6 CLAUDE.md rule'; $current = '6/6 store'
# 6. store
Write-Output "[6/6] Store.init at $LearnedHome (creates inbox/global/topics/projects, tags.json, git repo)"
if (-not $DryRun) {
    $env:LEARNED_HOME = $LearnedHome
    & $Python -c 'from learned.config import learned_home; from learned.store import Store; Store(learned_home()).init()'
    if ($LASTEXITCODE -ne 0) { throw 'Store.init failed' }
}
$completed += '6/6 store'
} catch {
    Write-Output ''
    Write-Output (Format-InstallFailure -Completed $completed -Failed $current -Reason $_.Exception.Message)
    exit 1
}

Write-Output ''
if ($DryRun) {
    Write-Output 'Dry run complete. Nothing was changed. The zvec search index is NOT built by this script.'
} else {
    Write-Output 'Installed. Restart open Claude Code sessions for hooks, skill and command to load.'
}
