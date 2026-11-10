param(
    [switch]$SkipTests,
    [switch]$SkipInstaller
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $repo '.venv\Scripts\python.exe'
$spec = Join-Path $PSScriptRoot 'LinkScope.spec'
$iss = Join-Path $PSScriptRoot 'LinkScope.iss'
$dist = Join-Path $repo 'dist'
$portableDir = Join-Path $dist 'LinkScope'
$releaseDir = Join-Path $dist 'release'
$portableZip = Join-Path $releaseDir 'NearLink-LinkScope-0.3.6-Portable.zip'

if (-not (Test-Path -LiteralPath $python)) {
    throw "缺少项目虚拟环境：$python"
}

if (-not $SkipTests) {
    & $python -m unittest discover -s (Join-Path $repo 'tools') -p 'test_*.py'
    if ($LASTEXITCODE -ne 0) { throw "tests failed ($LASTEXITCODE)" }
}

Push-Location $repo
try {
    & $python -m PyInstaller --noconfirm --clean --distpath $dist --workpath (Join-Path $repo 'build\pyinstaller') $spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
} finally {
    Pop-Location
}

$exe = Join-Path $portableDir 'LinkScope.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw "缺少打包产物：$exe" }

New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
if (Test-Path -LiteralPath $portableZip) { Remove-Item -LiteralPath $portableZip -Force }
Compress-Archive -LiteralPath $portableDir -DestinationPath $portableZip -CompressionLevel Optimal

if (-not $SkipInstaller) {
    $isccCandidates = @(
        'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
        'C:\Program Files\Inno Setup 6\ISCC.exe',
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
    )
    $iscc = $isccCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $iscc) { throw '未找到 Inno Setup 6 ISCC.exe' }
    & $iscc $iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed ($LASTEXITCODE)" }
}

$releaseFiles = @($portableZip)
if (-not $SkipInstaller) {
    $releaseFiles += Join-Path $releaseDir 'NearLink-LinkScope-0.3.6-Setup.exe'
}
$checksumLines = $releaseFiles | ForEach-Object {
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $_).Hash.ToLowerInvariant()
    "$hash  $([System.IO.Path]::GetFileName($_))"
}
[System.IO.File]::WriteAllLines((Join-Path $releaseDir 'SHA256SUMS.txt'), $checksumLines,
    [System.Text.UTF8Encoding]::new($false))

Get-ChildItem -LiteralPath $releaseDir | Select-Object Name, Length, LastWriteTime
