param(
    [switch]$SkipTests,
    [switch]$SkipInstaller
)

# 版本号的唯一来源是 tools/nlscope.py 的 APP_VERSION（窗口标题、run.json 也用它）。
# 本脚本负责把它翻译成 PyInstaller 资源版本、Inno Setup 版本号和产物文件名，
# 避免出现"程序里写 M5.0.9-rc5、安装包叫 0.3.6"这种对不上的情况。

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $repo '.venv\Scripts\python.exe'
$spec = Join-Path $PSScriptRoot 'LinkScope.spec'
$iss = Join-Path $PSScriptRoot 'LinkScope.iss'
$dist = Join-Path $repo 'dist'
$portableDir = Join-Path $dist 'LinkScope'
$releaseDir = Join-Path $dist 'release'
$workDir = Join-Path $repo 'build\pyinstaller'

if (-not (Test-Path -LiteralPath $python)) {
    throw "缺少项目虚拟环境：$python"
}

$nlscope = Join-Path $repo 'tools\nlscope.py'
$match = Select-String -LiteralPath $nlscope -Pattern '^APP_VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $match) {
    throw "tools\nlscope.py 里找不到 APP_VERSION 常量"
}
$appVersion = $match.Matches[0].Groups[1].Value

# Windows 文件资源版本必须是四段数字：M5.0.9-rc5 -> 5, 0, 9, 5
if ($appVersion -match '^[A-Za-z]*(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?$') {
    $rc = if ($Matches[4]) { [int]$Matches[4] } else { 0 }
    $v = @([int]$Matches[1], [int]$Matches[2], [int]$Matches[3], $rc)
} else {
    $v = @(0, 0, 0, 0)
}
$versionTuple = ($v -join ', ')
$versionText = ($v -join '.')
$portableZip = Join-Path $releaseDir "NearLink-LinkScope-$appVersion-Portable.zip"
$setupExe = Join-Path $releaseDir "NearLink-LinkScope-$appVersion-Setup.exe"

Write-Host "应用版本 $appVersion（Windows 资源版本 $versionText）"

if (-not $SkipTests) {
    & $python -m unittest discover -s (Join-Path $repo 'tools') -p 'test_*.py'
    if ($LASTEXITCODE -ne 0) { throw "tests failed ($LASTEXITCODE)" }
    & $python -m unittest discover -s (Join-Path $repo 'tests') -p 'test_*.py'
    if ($LASTEXITCODE -ne 0) { throw "tests failed ($LASTEXITCODE)" }
}

# 每次构建按当前 APP_VERSION 重新生成资源版本文件，不把生成物写回仓库
New-Item -ItemType Directory -Force -Path $workDir | Out-Null
$versionInfo = Join-Path $workDir 'version_info.txt'
$versionInfoBody = @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($versionTuple),
    prodvers=($versionTuple),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'080404B0',
        [StringStruct(u'CompanyName', u'Qjh213'),
         StringStruct(u'FileDescription', u'NearLink LinkScope SLE/BLE 链路分析工具'),
         StringStruct(u'FileVersion', u'$appVersion'),
         StringStruct(u'InternalName', u'LinkScope'),
         StringStruct(u'LegalCopyright', u'Copyright 2026 Qjh213'),
         StringStruct(u'OriginalFilename', u'LinkScope.exe'),
         StringStruct(u'ProductName', u'NearLink LinkScope'),
         StringStruct(u'ProductVersion', u'$appVersion')])
    ]),
    VarFileInfo([VarStruct(u'Translation', [2052, 1200])])
  ]
)
"@
Set-Content -LiteralPath $versionInfo -Value $versionInfoBody -Encoding utf8NoBOM

Push-Location $repo
try {
    $env:LINKSCOPE_VERSION_FILE = $versionInfo
    & $python -m PyInstaller --noconfirm --clean --distpath $dist --workpath $workDir $spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
} finally {
    Pop-Location
    Remove-Item Env:\LINKSCOPE_VERSION_FILE -ErrorAction SilentlyContinue
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
    & $iscc "/DMyAppVersion=$appVersion" $iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed ($LASTEXITCODE)" }
    if (-not (Test-Path -LiteralPath $setupExe)) { throw "缺少安装包：$setupExe" }
}

$releaseFiles = @($portableZip)
if (-not $SkipInstaller) { $releaseFiles += $setupExe }
$checksumLines = $releaseFiles | ForEach-Object {
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $_).Hash.ToLowerInvariant()
    "$hash  $([System.IO.Path]::GetFileName($_))"
}
[System.IO.File]::WriteAllLines((Join-Path $releaseDir 'SHA256SUMS.txt'), $checksumLines,
    [System.Text.UTF8Encoding]::new($false))

Get-ChildItem -LiteralPath $releaseDir | Select-Object Name, Length, LastWriteTime
