<#
.SYNOPSIS
    打开/关闭 QCOM 的"日志存文件"，并指定存到哪个文件。

.DESCRIPTION
    QCOM_V1.6 把设置存在 E:\Starklink\Config.ini（GBK 编码），控制存日志的是
        Enable_Save_Log=1     ← 开
        SaveAsLog=1
        LogPath=...\xxx.txt
    注意两点：
      1. QCOM 退出时会把内存里的设置**写回** Config.ini，所以必须先关 QCOM 再改，
         否则改完一关就被覆盖（脚本会检查进程，发现 QCOM 在跑就直接拒绝）；
      2. Config.ini 是 GBK 编码，路径里有中文，所以这里显式用 936 代码页读写，
         不要用默认的 UTF-8，否则中文路径会变成乱码、QCOM 就找不到目录了。

.EXAMPLE
    .\qcom-log.ps1 -Action on -Path 'E:\Starklink\日志\QCOM_LOG_A板.txt'
    .\qcom-log.ps1 -Action status
    .\qcom-log.ps1 -Action off
#>
[CmdletBinding()]
param(
    [ValidateSet('on', 'off', 'status')]
    [string]$Action = 'status',

    [string]$Path = 'E:\Starklink\日志\QCOM_LOG_A板.txt',

    [string]$IniPath = 'E:\Starklink\Config.ini',

    # QCOM 在跑时默认拒绝改写（它退出会把内存里的设置写回、覆盖本次改动）。
    # 确认 QCOM 已关、或只是想拿副本做测试时加 -Force。
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$gbk = [System.Text.Encoding]::GetEncoding(936)

function Read-Ini {
    param([string]$File)
    if (-not (Test-Path -LiteralPath $File)) { throw "找不到 $File" }
    return [System.IO.File]::ReadAllLines($File, $gbk)
}

function Show-Ini {
    param([string[]]$Lines)
    $Lines | Where-Object { $_ -match '^\s*(Enable_Save_Log|SaveAsLog|LogPath|Baudrate)\s*=' } |
        ForEach-Object { "  $_" }
}

if ($Action -eq 'status') {
    "QCOM 日志设置（$IniPath）："
    Show-Ini (Read-Ini $IniPath)
    $p = Get-Process -Name 'QCOM_V1.6' -ErrorAction SilentlyContinue
    if ($p) { "  [注意] QCOM 正在运行，退出时会覆盖这里的改动" }
    return
}

$proc = Get-Process -Name 'QCOM_V1.6' -ErrorAction SilentlyContinue
if ($proc -and -not $Force) {
    throw "QCOM 正在运行（PID $($proc.Id -join ',')）。先关掉 QCOM 再执行本脚本，否则退出时会把设置写回覆盖；确认无妨可加 -Force。"
}

$lines = Read-Ini $IniPath
$want = ($Action -eq 'on')

function Set-Key {
    param([string[]]$Src, [string]$Key, [string]$Value)
    $found = $false
    $out = foreach ($l in $Src) {
        if ($l -match "^\s*$([regex]::Escape($Key))\s*=") { $found = $true; "$Key=$Value" }
        else { $l }
    }
    if (-not $found) { $out = @($out) + "$Key=$Value" }
    return $out
}

if ($want) {
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $dir)) { throw "目录不存在：$dir" }
}

$lines = @(Set-Key $lines 'Enable_Save_Log' ([int]$want))
$lines = @(Set-Key $lines 'SaveAsLog' '1')
if ($want) { $lines = @(Set-Key $lines 'LogPath' $Path) }

Copy-Item -LiteralPath $IniPath -Destination "$IniPath.bak" -Force
[System.IO.File]::WriteAllLines($IniPath, $lines, $gbk)

"已写回 $IniPath（备份 $IniPath.bak）："
Show-Ini (Read-Ini $IniPath)
if ($want) {
    ""
    "接下来：启动 QCOM → 打开串口 → 每次跑实验前把 $Path 改名或删掉，避免和上一轮混在一起。"
}
