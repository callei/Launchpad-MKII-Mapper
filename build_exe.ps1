# PowerShell build script for packaging the Launchpad Mapper into a standalone exe
# Requirements: pip install pyinstaller
# Optional: place icons/app.ico for custom icon

param(
  [switch]$Clean,
  [string]$Version,  # Om inte satt: läses från pyproject.toml
  [string]$Company = 'malm',
  [string]$Product = 'Launchpad MKII Mapper',
  [string]$Copyright = '(c) 2025 Carl Jagemalm',
  [string]$Description = 'Launchpad pad mapping & animation tool'
)

$ErrorActionPreference = 'Stop'

if ($Clean) {
  Write-Host 'Cleaning dist/ and build/ ...'
  if (Test-Path dist) { Remove-Item dist -Recurse -Force }
  if (Test-Path build) { Remove-Item build -Recurse -Force }
}

### Välj Python-exekverare (föredra lokal venv)
$pythonExe = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path $pythonExe)) { $pythonExe = 'python' }

# Ensure dependencies installed (i vald tolk)
Write-Host 'Ensuring dependencies installed (PySide6, mido, python-rtmidi, pyyaml, pywin32)...'
& $pythonExe -m pip install --disable-pip-version-check PySide6 mido python-rtmidi pyyaml pywin32 | Out-Null

$iconPath = Join-Path $PSScriptRoot 'icons/app.ico'

# Läs version från pyproject.toml om ej specificerad
$pyprojectPath = Join-Path $PSScriptRoot 'pyproject.toml'
if (-not $Version) {
  if (Test-Path $pyprojectPath) {
    $match = Select-String -Path $pyprojectPath -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($match) {
      $Version = $match.Matches[0].Groups[1].Value
    }
  }
  if (-not $Version) { $Version = '0.0.0' }
}
Write-Host "Using version: $Version" -ForegroundColor DarkCyan

# Generera version resource fil (alltid, för att uppdatera versionen)
$versionFile = Join-Path $PSScriptRoot 'file_version_info.txt'
## Bygg tuple för versionsinfo (fyll på med nollor till 4 delar)
$verTuple = ($Version.Split('.') + '0','0','0')[0..3]
@"
# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($($verTuple -join ',')),
    prodvers=($($verTuple -join ',')),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(u'040904B0', [
        StringStruct(u'CompanyName', u'$Company'),
        StringStruct(u'FileDescription', u'$Description'),
        StringStruct(u'FileVersion', u'$Version'),
        StringStruct(u'InternalName', u'LaunchpadMapper'),
        StringStruct(u'LegalCopyright', u'$Copyright'),
        StringStruct(u'OriginalFilename', u'LaunchpadMapper.exe'),
        StringStruct(u'ProductName', u'$Product'),
        StringStruct(u'ProductVersion', u'$Version')
      ])
    ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -Encoding UTF8 $versionFile

$pyArgs = @('--name','LaunchpadMapper','--noconfirm','--clean','--version-file', $versionFile)
## Kör utan konsolfönster (windowed)
$pyArgs += @('--noconsole')
if (Test-Path $iconPath) { $pyArgs += @('--icon', $iconPath) }
$pyArgs += @('--onedir','--exclude-module','tests','--exclude-module','__pycache__')
$pyArgs += @('--add-data','fonts;fonts','--add-data','presets;presets')
if (Test-Path 'icons') { $pyArgs += @('--add-data','icons;icons') }
if (Test-Path 'config.yaml') { $pyArgs += @('--add-data','config.yaml;.') }
# Hidden imports for dynamic modules
$pyArgs += @('--hidden-import','rtmidi','--hidden-import','rtmidi._rtmidi','--hidden-import','mido.backends.rtmidi','--hidden-import','importlib_metadata')
# Ensure win32com is included when pywin32 is present
$pyArgs += @('--hidden-import','win32com','--hidden-import','win32com.client')

# Locate _rtmidi*.pyd and include explicitly (PyInstaller missar ibland den via importscanning)
$rtmidiDir = Join-Path $PSScriptRoot '.venv/Lib/site-packages/rtmidi'
if (Test-Path $rtmidiDir) {
  $pyd = Get-ChildItem $rtmidiDir -Filter '_rtmidi*.pyd' -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($pyd) {
    Write-Host "Including rtmidi binary: $($pyd.FullName)" -ForegroundColor DarkYellow
    $pyArgs += @('--add-binary', ("{0};rtmidi" -f $pyd.FullName))
  } else {
    Write-Warning 'Could not find _rtmidi*.pyd; MIDI may fail in frozen build.'
  }
} else {
  Write-Warning 'rtmidi package directory not found; skipping binary include.'
}
# Script sist
$pyArgs += 'gui/launchpad_mapper.py'

Write-Host "Running: $pythonExe -m PyInstaller $($pyArgs -join ' ')" -ForegroundColor Cyan
& $pythonExe -m PyInstaller @pyArgs

if ($LASTEXITCODE -ne 0) {
  Write-Error 'PyInstaller build failed.'
  exit 1
}

Write-Host 'Build complete.' -ForegroundColor Green
Write-Host 'Output:' (Get-ChildItem dist | Select-Object -ExpandProperty Name)

Write-Host 'One-dir build completed (one-file variant borttagen).' -ForegroundColor DarkGray
