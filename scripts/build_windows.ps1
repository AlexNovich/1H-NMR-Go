$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
function Check-Exit { if ($LASTEXITCODE -ne 0) { throw "Command failed: $LASTEXITCODE" } }
if ($env:OS -ne 'Windows_NT') { throw 'Build on Windows x64; cross-compilation is unsupported' }
$sourceCommit = (git rev-parse HEAD); Check-Exit
if (git status --porcelain --untracked-files=no) { throw 'Commit tracked changes before building' }
# Every build gets a new environment; no shared/system package changes.
$buildEnv = Join-Path $PWD ('build/windows-env-' + [guid]::NewGuid().ToString('N'))
py -3.11 -m venv $buildEnv; Check-Exit
$python = Join-Path $buildEnv 'Scripts/python.exe'
& $python -c "import struct; assert struct.calcsize('P') == 8"; Check-Exit
& $python -m pip install --only-binary=:all: --index-url https://pypi.org/simple --require-hashes -r requirements/win-amd64.lock; Check-Exit
& $python -m pip install --no-deps --no-build-isolation -e .; Check-Exit
& $python scripts/check_no_sensitive_data.py --tracked; Check-Exit
$env:QT_QPA_PLATFORM = 'offscreen'
& $python -m pytest -q; Check-Exit
& $python -m ruff check src tests scripts/check_no_sensitive_data.py scripts/package_report.py scripts/benchmark_editor.py scripts/write_app_icon.py; Check-Exit
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force build/package-inputs | Out-Null
& $python scripts/benchmark_editor.py build/package-inputs/editor-benchmark.json; Check-Exit
& $python scripts/write_app_icon.py build/package-inputs/nmr-go.ico; Check-Exit
$appVersion = (& $python -c "from bruker_nmr import __version__; print(__version__)"); Check-Exit
$identity = @{version=$appVersion; source_commit=$sourceCommit; build_kind='Windows x64 release candidate 8'}
$identity | ConvertTo-Json | Set-Content -Encoding utf8 build/package-inputs/build_identity.json
& $python -m PyInstaller packaging/NMR-go.spec --noconfirm --clean; Check-Exit
& $python scripts/package_report.py; Check-Exit
Compress-Archive -Path dist/NMR-go -DestinationPath dist/NMR-go-windows-x64.zip -Force
(Get-FileHash dist/NMR-go-windows-x64.zip).Hash.ToLower() | Set-Content dist/NMR-go-windows-x64.zip.sha256
Get-Item dist/NMR-go-windows-x64.zip | Select-Object Name, Length
