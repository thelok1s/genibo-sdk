# Install a built setup.exe silently for the current user, self-test the installed app, uninstall.
#   pwsh packaging/windows/verify-installer.ps1 dist/release/Genibo-Studio-0.1.0-windows-x64-setup.exe
param([Parameter(Mandatory = $true)][string]$Setup)
$ErrorActionPreference = "Stop"
$dir = Join-Path $env:RUNNER_TEMP "GeniboStudioInstall"
if (-not $env:RUNNER_TEMP) { $dir = Join-Path $env:TEMP "GeniboStudioInstall" }
$p = Start-Process -FilePath $Setup -Wait -PassThru -ArgumentList @(
    "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER", "/DIR=`"$dir`"", "/LOG=`"$dir.install.log`"")
if ($p.ExitCode -ne 0) { Get-Content "$dir.install.log" -ErrorAction SilentlyContinue; throw "installer exit code $($p.ExitCode)" }
$exe = Join-Path $dir "genibo-studio.exe"
if (-not (Test-Path $exe)) { throw "installed executable not found: $exe" }
$log = Join-Path $dir "..\installed-selftest.log"
$env:QT_QPA_PLATFORM = "offscreen"
$t = Start-Process -FilePath $exe -Wait -PassThru -ArgumentList @("--self-test", "--log", "`"$log`"")
Get-Content $log -ErrorAction SilentlyContinue
if ($t.ExitCode -ne 0) { throw "installed app self-test failed ($($t.ExitCode))" }
$u = Start-Process -FilePath (Join-Path $dir "unins000.exe") -Wait -PassThru -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
if ($u.ExitCode -ne 0) { throw "uninstaller exit code $($u.ExitCode)" }
Write-Host "INSTALLER OK: $Setup"
