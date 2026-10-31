<#
MediaForge installer for Windows 10/11. Open Windows PowerShell (Start, type powershell) and run:

    irm is.gd/MediaForge | iex

(is.gd/MediaForge is a short link to https://raw.githubusercontent.com/drunkgummyboy/Media-Forge/main/install.ps1)

(Not via the Run dialog: security software blocks download commands started from Win+R, because
scam pages ask people to paste them there.)

Sets up winget (Windows' app installer) when it is missing, installs Python 3.13 when no
Python 3.13+ is found, downloads MediaForge into %LOCALAPPDATA%\Programs\MediaForge, gives it its
own Python environment (.venv) with the packages it needs, checks that it starts, adds Start menu
and desktop shortcuts, registers it under Windows' "Installed apps" (current user; uninstaller:
src\mediaforge\uninstall.ps1), turns the Explorer right-click menu on and starts it. Running it
again reinstalls the latest version. Settings and history live in %APPDATA%\MediaForge and
%LOCALAPPDATA%\MediaForge and are never touched.

Ported from AirGrabber's installer. Written for Windows PowerShell 5.1 (what a new PC has): no &&,
ternaries or ?? operators, and no exit (under irm | iex it would close the user's window).
ASCII only, so a copy run with -File (no BOM) reads the same.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\MediaForge'),
    [string]$Archive,      # install from this .zip instead of downloading it (testing)
    [switch]$NoShortcuts,
    [switch]$NoLaunch,
    [switch]$NoMenu,
    [string]$UninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\MediaForge'
)

$Repo = 'drunkgummyboy/Media-Forge'
$Branch = 'main'
$ArchiveUrl = "https://codeload.github.com/$Repo/zip/refs/heads/$Branch"
$AppEntries = @('main.py', 'pyproject.toml', 'src')    # keep in step with platform/update_files.py
$IconPath = 'src\mediaforge\assets\MediaForge.ico'     # keep in step with platform/install_info.py
$Uninstaller = 'src\mediaforge\uninstall.ps1'          # keep in step with platform/install_info.py
$AppId = 'drunkgummyboy.MediaForge'                    # keep in step with platform/identity.py
$AppIdHelper = 'src\mediaforge\shortcut_app_id.cs'
$PythonFallback = '3.13.15'                            # python.org build used when winget can't
# winget for PCs without it: App Installer (which brings winget.exe) from winget's own release, plus
# the frameworks that release ships in DesktopAppInstaller_Dependencies.zip. Pinned, so the two
# downloads always belong together.
$WingetRelease = 'https://github.com/microsoft/winget-cli/releases/download/v1.29.380'
$AppInstallerFamily = 'Microsoft.DesktopAppInstaller_8wekyb3d8bbwe'

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Add-WindowsAppsToPath {
    # winget.exe is an app alias in %LOCALAPPDATA%\Microsoft\WindowsApps, which a fresh session's
    # PATH may not have yet.
    $apps = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps'
    $path = [string]$env:PATH
    foreach ($entry in $path.Split(';')) {
        if ($entry.Trim().TrimEnd('\') -eq $apps) { return }
    }
    if ($path) { $env:PATH = $path.TrimEnd(';') + ';' + $apps } else { $env:PATH = $apps }
}

function Test-Winget {
    return [bool](Get-Command winget.exe -ErrorAction SilentlyContinue)
}

function Wait-Winget([int]$Seconds) {
    # $true once winget.exe can be found; Windows may add the alias a moment after an install.
    for ($second = 0; $second -lt $Seconds; $second++) {
        if (Test-Winget) { return $true }
        Start-Sleep -Seconds 1
    }
    return (Test-Winget)
}

function Get-WingetArchitecture {
    # winget's frameworks come per processor: arm64 on an ARM PC, else x64.
    if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { return 'arm64' }
    return 'x64'
}

function Install-WingetFromGitHub {
    # Downloads App Installer and its frameworks from winget's release ($WingetRelease) and adds the
    # frameworks for this PC's processor, then App Installer. Throws when App Installer could not be
    # added. The downloads are removed either way.
    $arch = Get-WingetArchitecture
    $work = Join-Path ([IO.Path]::GetTempPath()) ('mediaforge-winget-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $work | Out-Null
    try {
        Write-Host '    Downloading it from Microsoft (about 300 MB, so this can take a few minutes).'
        $frameworksZip = Join-Path $work 'DesktopAppInstaller_Dependencies.zip'
        $bundle = Join-Path $work "$AppInstallerFamily.msixbundle"
        Invoke-WebRequest -Uri "$WingetRelease/DesktopAppInstaller_Dependencies.zip" -OutFile $frameworksZip -UseBasicParsing
        Invoke-WebRequest -Uri "$WingetRelease/$AppInstallerFamily.msixbundle" -OutFile $bundle -UseBasicParsing
        $unpacked = Join-Path $work 'dependencies'
        Expand-Archive -LiteralPath $frameworksZip -DestinationPath $unpacked -Force
        # By name: both VCLibs come before the Windows App Runtime.
        $frameworks = @(Get-ChildItem -LiteralPath (Join-Path $unpacked $arch) -Filter '*.appx' | Sort-Object Name)
        if ($frameworks.Count -eq 0) { throw "winget's download has no frameworks for $arch." }
        foreach ($framework in $frameworks) {
            try {
                $null = Add-AppxPackage -Path $framework.FullName
            } catch {
                # Mostly "a higher version is already installed", which App Installer can use too.
                Write-Verbose ('Framework ' + $framework.Name + ' not added: ' + $_.Exception.Message)
            }
        }
        $null = Add-AppxPackage -Path $bundle
    } finally {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Initialize-Winget {
    # Makes sure winget is there (it installs Python): registers the App Installer that comes with
    # Windows but may not be set up yet (common on a new Windows 11), else downloads it from
    # Microsoft. Never fails the install: without winget, Python comes from python.org. Returns
    # 'found', 'registered', 'downloaded' or 'failed'.
    $ErrorActionPreference = 'Stop'  # every failure below lands in a catch
    try {
        Add-WindowsAppsToPath
    } catch {
        Write-Verbose ('WindowsApps not added to PATH: ' + $_.Exception.Message)
    }
    if (Test-Winget) { return 'found' }

    Write-Step "Getting Windows' app installer (winget)..."
    $registered = $false
    try {
        $null = Add-AppxPackage -RegisterByFamilyName -MainPackage $AppInstallerFamily
        $registered = $true
    } catch {
        Write-Verbose ('App Installer not registered: ' + $_.Exception.Message)
    }
    $wait = 0
    if ($registered) { $wait = 5 }
    if (Wait-Winget $wait) { return 'registered' }

    try {
        $null = Install-WingetFromGitHub
        if (Wait-Winget 10) { return 'downloaded' }
    } catch {
        Write-Verbose ('winget not downloaded: ' + $_.Exception.Message)
    }
    Write-Host "Couldn't add winget; continuing without it." -ForegroundColor Yellow
    return 'failed'
}

function Test-Python([string]$Exe, [switch]$Tested) {
    # The real interpreter path when $Exe is Python 3.13+ with tkinter and venv (with -Tested:
    # exactly 3.13, the version MediaForge is tested on), else $null. The Microsoft Store's
    # python.exe placeholder (which only opens the Store) fails the probe and is skipped.
    if (-not $Exe -or -not (Test-Path -LiteralPath $Exe)) { return $null }
    # In Windows PowerShell 5.1, stderr from a redirected native command is a terminating error
    # under 'Stop'; a Python without tkinter must just be skipped.
    $ErrorActionPreference = 'Continue'
    $wanted = 'sys.version_info[:2] == (3, 13)'
    if (-not $Tested) { $wanted = 'sys.version_info >= (3, 13)' }
    # The path travels as hex of its UTF-8 bytes: Python and Windows PowerShell 5.1 use different
    # code pages for a pipe, which garbled user folders like "Juergen" spelt with an umlaut.
    $probe = "import sys, tkinter, venv; print(sys.executable.encode('utf-8').hex()) if $wanted else sys.exit(1)"
    $real = & $Exe -c $probe 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $real) { return $null }
    $hex = ([string]($real | Select-Object -Last 1)).Trim()
    if ($hex.Length -eq 0 -or ($hex.Length % 2) -ne 0) { return $null }
    $bytes = New-Object byte[] ($hex.Length / 2)
    for ($i = 0; $i -lt $bytes.Length; $i++) {
        $bytes[$i] = [Convert]::ToByte($hex.Substring($i * 2, 2), 16)
    }
    return [Text.Encoding]::UTF8.GetString($bytes)
}

function Find-Python {
    $ErrorActionPreference = 'Continue'  # see Test-Python
    $candidates = New-Object System.Collections.Generic.List[string]
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        # "py -0p" lists every registered install: " -V:3.13 *   C:\...\python.exe"
        foreach ($line in (& $launcher.Source -0p 2>$null)) {
            if ($line -match '([A-Za-z]:\\.+?python\.exe)\s*$') { $candidates.Add($Matches[1]) }
        }
    }
    foreach ($root in @((Join-Path $env:LOCALAPPDATA 'Programs\Python'), $env:ProgramFiles)) {
        if ($root -and (Test-Path -LiteralPath $root)) {
            Get-ChildItem -LiteralPath $root -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue |
                Sort-Object Name -Descending |
                ForEach-Object { $candidates.Add((Join-Path $_.FullName 'python.exe')) }
        }
    }
    foreach ($command in @(Get-Command python.exe -All -ErrorAction SilentlyContinue)) {
        $candidates.Add($command.Source)
    }
    # Prefer the tested Python 3.13; a newer one is only used when 3.13 isn't there.
    foreach ($candidate in $candidates) {
        $python = Test-Python $candidate -Tested
        if ($python) { return $python }
    }
    foreach ($candidate in $candidates) {
        $python = Test-Python $candidate
        if ($python) { return $python }
    }
    return $null
}

function Install-Python {
    Write-Step 'Installing Python, which MediaForge runs on (only needed once; this can take a few minutes)'
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        # Out-Host: winget's output must not become part of this function's return value.
        & $winget.Source install --id Python.Python.3.13 --exact --source winget --scope user --silent `
            --accept-package-agreements --accept-source-agreements | Out-Host
        $python = Find-Python
        if ($python) { return $python }
        Write-Host 'That did not work; trying another way to install Python.' -ForegroundColor Yellow
    }
    $url = "https://www.python.org/ftp/python/$PythonFallback/python-$PythonFallback-amd64.exe"
    $setup = Join-Path ([IO.Path]::GetTempPath()) "python-$PythonFallback-amd64.exe"
    Invoke-WebRequest -Uri $url -OutFile $setup -UseBasicParsing
    try {
        $process = Start-Process -FilePath $setup -Wait -PassThru -ArgumentList @(
            '/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_launcher=1', 'Include_test=0'
        )
        if ($process.ExitCode -ne 0) { throw "Python could not be installed (error code $($process.ExitCode))." }
    } finally {
        Remove-Item -LiteralPath $setup -Force -ErrorAction SilentlyContinue
    }
    $python = Find-Python
    if (-not $python) { throw 'Python was installed, but cannot be found yet. Please run the install command again.' }
    return $python
}

function Test-SamePath([string]$A, [string]$B) {
    if (-not $A -or -not $B) { return $false }
    try {
        $left = [IO.Path]::GetFullPath($A).TrimEnd('\')
        $right = [IO.Path]::GetFullPath($B).TrimEnd('\')
        return [string]::Equals($left, $right, [StringComparison]::OrdinalIgnoreCase)
    } catch {
        return $false
    }
}

function Test-AppRunning([string]$MainPy) {
    # $true when a MediaForge from this folder is running (its files are about to be replaced).
    $all = @(Get-CimInstance -ClassName Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" -ErrorAction SilentlyContinue)
    foreach ($process in $all) {
        $line = $process.CommandLine
        if ($line -and $line.IndexOf($MainPy, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
    }
    return $false
}

function Install-App {
    $work = Join-Path ([IO.Path]::GetTempPath()) ('mediaforge-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $work | Out-Null
    try {
        if ($Archive) {
            $zip = (Resolve-Path -LiteralPath $Archive).Path
        } else {
            Write-Step 'Downloading MediaForge'
            $zip = Join-Path $work 'mediaforge.zip'
            Invoke-WebRequest -Uri $ArchiveUrl -OutFile $zip -UseBasicParsing
        }
        $unpacked = Join-Path $work 'unpacked'
        Expand-Archive -LiteralPath $zip -DestinationPath $unpacked -Force
        $source = @(Get-ChildItem -LiteralPath $unpacked -Directory)
        if ($source.Count -ne 1) { throw 'The download looks damaged. Please try again.' }
        foreach ($entry in $AppEntries) {
            if (-not (Test-Path -LiteralPath (Join-Path $source[0].FullName $entry))) {
                throw "The download is incomplete ($entry is missing). Please try again."
            }
        }
        Write-Step 'Copying MediaForge to your computer'
        New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
        foreach ($entry in $AppEntries) {
            $target = Join-Path $InstallDir $entry
            if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
            Copy-Item -LiteralPath (Join-Path $source[0].FullName $entry) -Destination $target -Recurse
        }
        # A staged update from before this reinstall is older than what was just copied.
        Remove-Item -LiteralPath (Join-Path $InstallDir '.update') -Recurse -Force -ErrorAction SilentlyContinue
    } finally {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Initialize-Venv([string]$Python) {
    # MediaForge's own Python environment in the install folder, so its packages never clash with
    # anything else on the PC. Reused on a reinstall; made again when its Python has gone.
    $venv = Join-Path $InstallDir '.venv'
    $venvPython = Join-Path $venv 'Scripts\python.exe'
    if ((Test-Path -LiteralPath $venvPython) -and (Test-Python $venvPython)) { return $venvPython }
    Write-Step "Setting up MediaForge's own Python environment"
    if (Test-Path -LiteralPath $venv) { Remove-Item -LiteralPath $venv -Recurse -Force }
    & $Python -m venv $venv | Out-Host
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
        throw "MediaForge's Python environment could not be created."
    }
    return $venvPython
}

function Install-Packages([string]$VenvPython) {
    Write-Step 'Installing the parts MediaForge needs (this can take a minute)'
    $ErrorActionPreference = 'Continue'  # pip's warnings on stderr must not stop the install
    $pyproject = Join-Path $InstallDir 'pyproject.toml'
    $list = "import sys, tomllib; print('\n'.join(tomllib.load(open(sys.argv[1], 'rb'))['project']['dependencies']))"
    $packages = @(& $VenvPython -c $list $pyproject | Where-Object { $_ -and $_.Trim() })
    if ($LASTEXITCODE -ne 0 -or $packages.Count -eq 0) { throw "MediaForge's package list could not be read." }
    # --no-warn-script-location: pip's "not on PATH" warnings are harmless but look alarming.
    & $VenvPython -m pip install --disable-pip-version-check --no-input --quiet --no-warn-script-location @packages | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw 'Some parts MediaForge needs could not be installed (see the messages above). Check your internet connection and try again.'
    }
}

function Test-App([string]$VenvPython) {
    Write-Step 'Checking that MediaForge starts'
    $ErrorActionPreference = 'Continue'
    & $VenvPython (Join-Path $InstallDir 'main.py') --self-test 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "MediaForge's self-check failed (code $LASTEXITCODE). Run the install command again; if it keeps failing, send the messages above to whoever helped you set this up."
    }
}

function New-Shortcut([string]$Path, [string]$PythonW) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($Path)
    $link.TargetPath = $PythonW
    $link.Arguments = '"' + (Join-Path $InstallDir 'main.py') + '"'
    $link.WorkingDirectory = $InstallDir
    $link.IconLocation = (Join-Path $InstallDir $IconPath) + ',0'
    $link.Description = 'Rename and organise video files'
    $link.Save()
    Set-ShortcutAppId $Path
}

function Set-ShortcutAppId([string]$Path) {
    # MediaForge's AppUserModelID (platform/identity.py) on the shortcut, so Windows ties its
    # taskbar button to this shortcut and not to another program that runs the same pythonw.exe.
    # The C# helper comes with the app just installed, compiled once per run. Optional: without
    # it the shortcut still works, and MediaForge sets the ID on itself when it starts.
    try {
        if (-not ('MediaForge.ShortcutAppId' -as [type])) {
            $helper = (Resolve-Path -LiteralPath (Join-Path $InstallDir $AppIdHelper)).ProviderPath
            Add-Type -TypeDefinition ([IO.File]::ReadAllText($helper))
        }
        [MediaForge.ShortcutAppId]::Set($Path, $AppId)
    } catch {
        Write-Verbose ('No AppUserModelID on ' + $Path + ': ' + $_.Exception.Message)
    }
}

function Register-Uninstaller {
    # The "Installed apps" entry (current user, so no administrator permission needed). Keep the
    # value names and formats in step with platform/install_info.py, which refreshes
    # DisplayVersion after a self-update.
    $root = (Resolve-Path -LiteralPath $InstallDir).Path.TrimEnd('\')
    $version = ''
    $init = Join-Path $root 'src\mediaforge\__init__.py'
    if (Test-Path -LiteralPath $init) {
        $match = Select-String -LiteralPath $init -Pattern '^__version__\s*=\s*[''"]([^''"]+)[''"]' |
            Select-Object -First 1
        if ($match) { $version = $match.Matches[0].Groups[1].Value }
    }
    $bytes = (Get-ChildItem -LiteralPath $root -Recurse -File -Force -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum).Sum
    if (-not $bytes) { $bytes = 0 }
    $sizeKb = [int][Math]::Ceiling($bytes / 1KB)
    $uninstall = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' +
        (Join-Path $root $Uninstaller) + '"'

    if (-not (Test-Path -LiteralPath $UninstallKey)) { New-Item -Path $UninstallKey -Force | Out-Null }
    $strings = [ordered]@{
        DisplayName          = 'MediaForge'
        DisplayVersion       = $version
        Publisher            = 'drunkgummyboy'
        DisplayIcon          = (Join-Path $root $IconPath)
        InstallLocation      = $root
        URLInfoAbout         = "https://github.com/$Repo"
        UninstallString      = $uninstall
        QuietUninstallString = $uninstall + ' -Quiet'
    }
    foreach ($name in $strings.Keys) {
        New-ItemProperty -LiteralPath $UninstallKey -Name $name -Value $strings[$name] -PropertyType String -Force | Out-Null
    }
    $dwords = [ordered]@{ NoModify = 1; NoRepair = 1; EstimatedSize = $sizeKb }
    foreach ($name in $dwords.Keys) {
        New-ItemProperty -LiteralPath $UninstallKey -Name $name -Value $dwords[$name] -PropertyType DWord -Force | Out-Null
    }
}

function Show-FailureDialog([string]$Text) {
    # $true when the failure window was shown (and closed), $false when no window could be shown.
    try {
        if (-not [Environment]::UserInteractive) { return $false }
        foreach ($argument in [Environment]::GetCommandLineArgs()) {
            $name = ([string]$argument).TrimStart('-', '/').ToLowerInvariant()
            if ($name.Length -ge 4 -and 'noninteractive'.StartsWith($name)) { return $false }  # -noni...
        }
        Add-Type -AssemblyName System.Windows.Forms
        $owner = New-Object System.Windows.Forms.Form
        $owner.TopMost = $true  # above the PowerShell window
        try {
            $null = [System.Windows.Forms.MessageBox]::Show($owner, $Text, "MediaForge couldn't be installed",
                [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Error)
        } finally {
            $owner.Dispose()
        }
        return $true
    } catch {
        return $false
    }
}

function Get-FailureText([Exception]$Exception) {
    # A download or network error (anywhere in the exception's chain), or anything else.
    $current = $Exception
    while ($current) {
        $type = $current.GetType().FullName
        if (@('System.Net.WebException', 'System.Net.Sockets.SocketException', 'System.Net.Http.HttpRequestException') -contains $type) {
            return 'Downloading failed. Check your internet connection and run the install command again.'
        }
        $current = $current.InnerException
    }
    return 'Something went wrong. Run the install command again; if it keeps failing, send the details below to whoever helped you set this up.'
}

function Install-MediaForge {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'  # Invoke-WebRequest crawls with a progress bar in 5.1
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    $mainPy = Join-Path $InstallDir 'main.py'
    if ((Test-Path -LiteralPath $mainPy) -and (Test-AppRunning $mainPy)) {
        throw 'MediaForge is open. Close it, then run the install command again.'
    }

    # winget installs Python below; there is a fallback without it.
    Write-Step 'Checking for Python'
    $python = Find-Python
    if ($python) {
        Write-Host '    Python is already installed.'
    } else {
        $null = Initialize-Winget
        $python = Install-Python
    }

    Install-App
    $venvPython = Initialize-Venv $python
    Install-Packages $venvPython
    Test-App $venvPython

    $pythonw = Join-Path (Split-Path -Parent $venvPython) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $pythonw)) { $pythonw = $venvPython }
    if (-not $NoShortcuts) {
        Write-Step 'Adding MediaForge to the Start menu and your desktop'
        New-Shortcut (Join-Path ([Environment]::GetFolderPath('Programs')) 'MediaForge.lnk') $pythonw
        New-Shortcut (Join-Path ([Environment]::GetFolderPath('Desktop')) 'MediaForge.lnk') $pythonw
    }

    Write-Step 'Adding MediaForge to Installed apps in Windows Settings'
    try {
        Register-Uninstaller
    } catch {
        # MediaForge itself works; only the Settings entry is missing.
        Write-Host '    MediaForge could not be added to Installed apps, but it is installed and works.' -ForegroundColor Yellow
    }

    if (-not $NoMenu) {
        Write-Step "Adding MediaForge to Explorer's right-click menu"
        $ErrorActionPreference = 'Continue'
        & $venvPython $mainPy --install-menu 2>&1 | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Write-Host '    The right-click menu could not be added; you can add it later under Settings in MediaForge.' -ForegroundColor Yellow
        }
        $ErrorActionPreference = 'Stop'
    }

    Write-Host ''
    if ($NoShortcuts) {
        Write-Host 'MediaForge is ready.' -ForegroundColor Green
    } else {
        Write-Host "MediaForge is ready. You'll find it in the Start menu and on your desktop." -ForegroundColor Green
    }
    Write-Host 'It updates itself.'
    if (-not $NoLaunch) {
        $shortcut = ''
        if (-not $NoShortcuts) { $shortcut = Join-Path ([Environment]::GetFolderPath('Programs')) 'MediaForge.lnk' }
        switch (Get-LaunchPlan (Test-Elevated) $shortcut) {
            'direct' { Start-Process -FilePath $pythonw -ArgumentList ('"' + $mainPy + '"') -WorkingDirectory $InstallDir }
            # Explorer opens the shortcut with normal rights, like a click on it.
            'explorer' { Start-Process -FilePath 'explorer.exe' -ArgumentList ('"' + $shortcut + '"') }
            default { Write-Host 'Open MediaForge from the Start menu.' }
        }
    }
}

function Test-Elevated {
    # $true when this PowerShell runs with administrator rights ("Run as administrator").
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        return ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch {
        return $false
    }
}

function Get-LaunchPlan([bool]$Elevated, [string]$Shortcut) {
    # How to start MediaForge at the end: never with administrator rights, because a MediaForge
    # started that way can't be reached by the normal launches that follow (the desktop shortcut,
    # the right-click menu), so they would open nothing.
    if (-not $Elevated) { return 'direct' }
    if ($Shortcut) { return 'explorer' }
    return 'none'
}

# No `exit` anywhere: under `irm | iex` it would close the user's PowerShell window.
try {
    Install-MediaForge
} catch {
    $failure = $_.Exception
    Write-Host ''
    Write-Host "Installation failed: $($failure.Message)" -ForegroundColor Red
    # A window says so too. Without one, keep the console window open so the message can be read (a
    # non-interactive host throws instead).
    $text = (Get-FailureText $failure) + "`n`nDetails: " + $failure.Message
    $shown = $false
    try { $shown = Show-FailureDialog $text } catch { $shown = $false }
    if (-not $shown) {
        try { $null = Read-Host 'Press Enter to close' } catch { }
    }
}
