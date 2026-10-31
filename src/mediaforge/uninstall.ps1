<#
MediaForge uninstaller (Windows PowerShell 5.1). Windows' "Installed apps" runs it through the
UninstallString that install.ps1 registers:

    powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "<install>\src\mediaforge\uninstall.ps1"

It removes the install folder it lives in (this file's folder's grandparent), this install's Start
menu and desktop shortcuts, its Explorer right-click menu and its "Installed apps" entry, and, when
the user agrees, the settings and history in %APPDATA%\MediaForge and %LOCALAPPDATA%\MediaForge
(or $env:MEDIAFORGE_HOME).

-Quiet: no dialogs, and the settings and history are kept (testing and QuietUninstallString).
-UninstallKey: another "Installed apps" key (testing).

A folder without main.py, or with a tests folder (a development copy), is never removed.
Ported from AirGrabber's uninstaller. No &&, ternaries or ?? operators (PowerShell 5.1).
#>
[CmdletBinding()]
param(
    [switch]$Quiet,
    [string]$UninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\MediaForge'
)

$Title = 'Uninstall MediaForge'
# The right-click menu (platform/context_menu.py BASES), under HKEY_CURRENT_USER.
$MenuKeys = @(
    'HKCU:\Software\Classes\*\shell\MediaForge',
    'HKCU:\Software\Classes\Directory\shell\MediaForge'
)

function Normalize-Path([string]$Path) {
    return [IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function Test-SamePath([string]$A, [string]$B) {
    if (-not $A -or -not $B) { return $false }
    try {
        return [string]::Equals((Normalize-Path $A), (Normalize-Path $B), [StringComparison]::OrdinalIgnoreCase)
    } catch {
        return $false
    }
}

function Show-Dialog([string]$Text, [string]$Buttons = 'OK', [string]$Icon = 'Information', [string]$Default = 'Button1') {
    # Returns the button pressed ('Yes', 'No', 'Retry', ...); in -Quiet only writes the text.
    Write-Host $Text
    if ($Quiet) { return 'None' }
    $owner = New-Object System.Windows.Forms.Form
    $owner.TopMost = $true  # the uninstaller runs with a hidden window; keep dialogs in front
    try {
        $result = [System.Windows.Forms.MessageBox]::Show($owner, $Text, $Title,
            [System.Windows.Forms.MessageBoxButtons]$Buttons,
            [System.Windows.Forms.MessageBoxIcon]$Icon,
            [System.Windows.Forms.MessageBoxDefaultButton]$Default)
        return $result.ToString()
    } finally {
        $owner.Dispose()
    }
}

function Get-AppProcesses([string]$MainPy) {
    $found = @()
    $all = @(Get-CimInstance -ClassName Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" -ErrorAction SilentlyContinue)
    foreach ($process in $all) {
        $line = $process.CommandLine
        if ($line -and $line.IndexOf($MainPy, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            $found += $process
        }
    }
    return $found
}

function Close-App([string]$MainPy) {
    # $true once no MediaForge from this folder is running; $false when the user gives up.
    while ($true) {
        $running = @(Get-AppProcesses $MainPy)
        if ($running.Count -eq 0) { return $true }
        foreach ($item in $running) {
            $process = Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue
            if ($process) { $null = $process.CloseMainWindow() }
        }
        $deadline = (Get-Date).AddSeconds(15)
        while ((Get-Date) -lt $deadline -and @(Get-AppProcesses $MainPy).Count -gt 0) {
            Start-Sleep -Milliseconds 500
        }
        if (@(Get-AppProcesses $MainPy).Count -eq 0) { return $true }
        if ($Quiet) {
            Write-Host 'MediaForge is still running, so it was not removed. Close it and try again.'
            return $false
        }
        $answer = Show-Dialog 'MediaForge is still open. Close MediaForge first, then press Retry.' 'RetryCancel' 'Warning'
        if ($answer -ne 'Retry') { return $false }
    }
}

function Get-FirstArgument([string]$Arguments) {
    # The text inside the first pair of quotes ('"<install>\main.py" --x' gives '<install>\main.py'),
    # else the whole trimmed text.
    if (-not $Arguments) { return '' }
    $text = $Arguments.Trim()
    if (-not $text.StartsWith('"')) { return $text }
    $end = $text.IndexOf('"', 1)
    if ($end -lt 0) { return $text.Substring(1) }
    return $text.Substring(1, $end - 1)
}

function Remove-Shortcut([string]$Path, [string]$MainPy) {
    # Only removes a shortcut that starts this install (never one for another copy).
    if (-not (Test-Path -LiteralPath $Path)) { return }
    try {
        $shell = New-Object -ComObject WScript.Shell
        $arguments = $shell.CreateShortcut($Path).Arguments
    } catch {
        return
    }
    if (-not $arguments) { return }
    if (Test-SamePath (Get-FirstArgument $arguments) $MainPy) {
        Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    }
}

function Remove-Menu([string]$MainPy) {
    # Removes the right-click menu when it starts this install (one for another copy stays). Done
    # here, not through Python, so it also works when Python has been removed already.
    foreach ($base in $MenuKeys) {
        $shellKey = $base + '\shell'
        if (-not (Test-Path -LiteralPath $shellKey)) { continue }
        $first = @(Get-ChildItem -LiteralPath $shellKey -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($first.Count -eq 0) { continue }
        $command = ''
        try {
            $command = [string](Get-ItemProperty -LiteralPath ($first[0].PSPath + '\command') -ErrorAction Stop).'(default)'
        } catch {
            continue
        }
        if ($command.IndexOf($MainPy, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            Remove-Item -LiteralPath $base -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Get-DataDirs {
    if ($env:MEDIAFORGE_HOME) { return @($env:MEDIAFORGE_HOME) }
    return @((Join-Path $env:APPDATA 'MediaForge'), (Join-Path $env:LOCALAPPDATA 'MediaForge'))
}

function Uninstall-MediaForge {
    $ErrorActionPreference = 'Stop'
    if (-not $Quiet) { Add-Type -AssemblyName System.Windows.Forms }

    $installDir = Normalize-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
    $mainPy = Join-Path $installDir 'main.py'
    if (-not (Test-Path -LiteralPath $mainPy -PathType Leaf)) {
        $null = Show-Dialog "This does not look like a MediaForge installation, so nothing was removed:`n$installDir" 'OK' 'Warning'
        return
    }
    if (Test-Path -LiteralPath (Join-Path $installDir 'tests')) {
        $null = Show-Dialog "This is a development copy of MediaForge, so nothing was removed:`n$installDir" 'OK' 'Warning'
        return
    }

    if (-not $Quiet) {
        $answer = Show-Dialog 'Uninstall MediaForge?' 'YesNo' 'Question'
        if ($answer -ne 'Yes') { return }
    }

    if (-not (Close-App $mainPy)) { return }

    $removeData = $false
    if (-not $Quiet) {
        $answer = Show-Dialog 'Also delete your settings and history? Your presets, TMDB key and the list of past renames (for undo) will be gone.' 'YesNo' 'Question' 'Button2'
        $removeData = ($answer -eq 'Yes')
    }

    # Leave the folder before deleting it.
    Set-Location -LiteralPath ([IO.Path]::GetTempPath())
    [Environment]::CurrentDirectory = [IO.Path]::GetTempPath()

    Remove-Menu $mainPy
    Remove-Shortcut (Join-Path ([Environment]::GetFolderPath('Programs')) 'MediaForge.lnk') $mainPy
    Remove-Shortcut (Join-Path ([Environment]::GetFolderPath('Desktop')) 'MediaForge.lnk') $mainPy
    Remove-Shortcut (Join-Path ([Environment]::GetFolderPath('Startup')) 'MediaForge.lnk') $mainPy

    Remove-Item -LiteralPath $installDir -Recurse -Force -ErrorAction SilentlyContinue
    $leftOver = Test-Path -LiteralPath $installDir

    if (Test-Path -LiteralPath $UninstallKey) {
        $location = (Get-ItemProperty -LiteralPath $UninstallKey -ErrorAction SilentlyContinue).InstallLocation
        if (Test-SamePath $location $installDir) {
            Remove-Item -LiteralPath $UninstallKey -Recurse -Force
        }
    }

    $dataLeftOver = @()
    if ($removeData) {
        foreach ($dataDir in (Get-DataDirs)) {
            if (Test-Path -LiteralPath $dataDir) {
                Remove-Item -LiteralPath $dataDir -Recurse -Force -ErrorAction SilentlyContinue
                if (Test-Path -LiteralPath $dataDir) { $dataLeftOver += $dataDir }
            }
        }
    }

    $message = 'MediaForge was removed. Python is still installed; you can remove it under Installed apps if nothing else needs it.'
    if ($leftOver) {
        $message += "`n`nSome files could not be removed. You can delete this folder yourself:`n$installDir"
    }
    if ($dataLeftOver.Count -gt 0) {
        $message += "`n`nSome of your settings and history could not be removed. You can delete these folders yourself:`n" + ($dataLeftOver -join "`n")
    }
    if ($Quiet) {
        Write-Host $message
    } else {
        $null = Show-Dialog $message 'OK' 'Information'
    }
}

try {
    Uninstall-MediaForge
} catch {
    $null = Show-Dialog "MediaForge could not be uninstalled: $($_.Exception.Message)" 'OK' 'Error'
}
