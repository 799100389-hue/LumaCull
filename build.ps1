$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $root
try {
    Write-Host "== 光筛 LumaCull build ==" -ForegroundColor Cyan
    Write-Host "Installing build requirements..."
    python -m pip install -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

    Write-Host "Running PyInstaller..."
    python -m PyInstaller --noconfirm --clean LumaCull.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

    $dist = Join-Path $root "dist\LumaCull"
    if (-not (Test-Path (Join-Path $dist "LumaCull.exe"))) {
        throw "Build output not found: $dist\LumaCull.exe"
    }

    Copy-Item -Force (Join-Path $root "README.md") (Join-Path $dist "README.md")
    Copy-Item -Force (Join-Path $root "THIRD_PARTY.md") (Join-Path $dist "THIRD_PARTY.md")
    Copy-Item -Force (Join-Path $root "LICENSE") (Join-Path $dist "LICENSE")
    Copy-Item -Recurse -Force (Join-Path $root "docs") (Join-Path $dist "docs")

    $zip = Join-Path $root "dist\LumaCull-Windows-x64.zip"
    if (Test-Path $zip) { Remove-Item -Force $zip }
    Compress-Archive -Path $dist -DestinationPath $zip -CompressionLevel Optimal

    $size = [math]::Round((Get-Item (Join-Path $dist "LumaCull.exe")).Length / 1MB, 2)
    Write-Host ""
    Write-Host "Build complete." -ForegroundColor Green
    Write-Host "  exe: $dist\LumaCull.exe ($size MB launcher)"
    Write-Host "  zip: $zip"
} finally {
    Pop-Location
}
