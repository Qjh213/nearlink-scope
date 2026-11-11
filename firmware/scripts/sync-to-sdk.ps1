<#
.SYNOPSIS
    把 firmware/linkscope 同步进 SDK，并接好 Kconfig / CMake / .config 三处挂钩。

.DESCRIPTION
    只在 SDK 里做三件事，且都可以回退（首次修改前会备份成 *.bak.linkscope）：
      1) 复制 application/samples/products/linkscope
      2) 在 products/Kconfig 里加 SAMPLE_SUPPORT_LINK_SCOPE 开关
      3) 在 products/CMakeLists.txt 里加 add_subdirectory_if_exist(linkscope)
    可选 -Role 会顺带改好 ws63_liteos_app.config，省得每次进 menuconfig 点。

.EXAMPLE
    .\sync-to-sdk.ps1 -Role initiator
    .\sync-to-sdk.ps1 -Role responder
    .\sync-to-sdk.ps1 -NoConfig          # 只同步代码，配置自己用 menuconfig 改
#>
[CmdletBinding()]
param(
    [string]$SdkRoot = 'Z:\home\bearpi\project\bearpi-pico_h3863-master',
    [string]$ProjectRoot = 'E:\nearlink-scope',
    [ValidateSet('none', 'initiator', 'responder', 'loopback', 'timing', 'monitor')]
    [string]$Role = 'none',
    [int]$TimeoutMs = 50,
    [int]$Hz = 100,
    [int]$Payload = 40,
    [int]$Window = 1,
    [int]$IntvSlot = 0,
    [ValidateSet('sle', 'ble')]
    [string]$Link = 'sle',
    [ValidateSet('request', 'command')]
    [string]$SleWriteMode = 'request',
    [ValidateSet('request', 'command')]
    [string]$BleWriteMode = 'command',
    [ValidateRange(6, 3200)]
    [int]$BleIntvUnit = 6,
    [switch]$Quiet,
    [switch]$NoConfig
)

$ErrorActionPreference = 'Stop'

$src = Join-Path $ProjectRoot 'firmware\linkscope'
$dst = Join-Path $SdkRoot 'application\samples\products\linkscope'
$kcfg = Join-Path $SdkRoot 'application\samples\products\Kconfig'
$cmk = Join-Path $SdkRoot 'application\samples\products\CMakeLists.txt'
$appcfg = Join-Path $SdkRoot 'build\config\target_config\ws63\menuconfig\acore\ws63_liteos_app.config'

if (-not (Test-Path $src)) { throw "找不到源码目录: $src" }
if (-not (Test-Path $kcfg)) { throw "找不到 SDK（$SdkRoot），检查 Z: 是否已挂载" }

function Write-TextLf([string]$path, [string]$text) {
    [System.IO.File]::WriteAllText($path, $text.Replace("`r`n", "`n"), (New-Object System.Text.UTF8Encoding($false)))
}

# ---------- 1. 复制源码 ----------
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Get-ChildItem $src -Recurse -File | Where-Object { $_.Name -ne '.keep' } | ForEach-Object {
    $rel = $_.FullName.Substring($src.Length + 1)
    $target = Join-Path $dst $rel
    New-Item -ItemType Directory -Force -Path (Split-Path $target -Parent) | Out-Null
    Copy-Item $_.FullName $target -Force
}
Write-Host "[1/3] 源码已同步 -> $dst" -ForegroundColor Green

# ---------- 2. Kconfig 挂钩 ----------
$kcfgText = Get-Content $kcfg -Raw
if ($kcfgText -notmatch 'SAMPLE_SUPPORT_LINK_SCOPE') {
    [System.IO.File]::Copy($kcfg, "$kcfg.bak.linkscope", $true)
    $block = @"

config SAMPLE_SUPPORT_LINK_SCOPE
    bool
    prompt "Support LinkScope (NearLink link profiler)."
    default n
    depends on ENABLE_PRODUCTS_SAMPLE
    help
        NearLink 链路体检仪：RTT/抖动/丢包，SLE 与 BLE 同板对照，噪声底自校准。

if SAMPLE_SUPPORT_LINK_SCOPE
menu "LinkScope Configuration"
    osource "application/samples/products/linkscope/Kconfig"
endmenu
endif


"@
    $anchor = "config SAMPLE_SUPPORT_SLE_GETAWAY"
    if ($kcfgText -match [regex]::Escape($anchor)) {
        $kcfgText = $kcfgText -replace [regex]::Escape($anchor), ($block + $anchor)
    } else {
        $kcfgText = $kcfgText + $block
    }
    Write-TextLf $kcfg $kcfgText
    Write-Host "[2/3] products/Kconfig 已加开关（备份 $kcfg.bak.linkscope）" -ForegroundColor Green
} else {
    Write-Host "[2/3] products/Kconfig 已有开关，跳过" -ForegroundColor DarkGray
}

# ---------- 3. CMake 挂钩 ----------
$cmkText = Get-Content $cmk -Raw
if ($cmkText -notmatch 'linkscope') {
    [System.IO.File]::Copy($cmk, "$cmk.bak.linkscope", $true)
    $block = @"
if(DEFINED CONFIG_SAMPLE_SUPPORT_LINK_SCOPE)
    add_subdirectory_if_exist(linkscope)
endif()

"@
    $anchor = 'set(SOURCES "${SOURCES}" PARENT_SCOPE)'
    if ($cmkText -match [regex]::Escape($anchor)) {
        $cmkText = $cmkText -replace [regex]::Escape($anchor), ($block + $anchor)
    } else {
        $cmkText = $cmkText + "`n" + $block
    }
    Write-TextLf $cmk $cmkText
    Write-Host "[3/3] products/CMakeLists.txt 已加子目录（备份 $cmk.bak.linkscope）" -ForegroundColor Green
} else {
    Write-Host "[3/3] products/CMakeLists.txt 已挂钩，跳过" -ForegroundColor DarkGray
}

# ---------- 4. 可选：直接改 .config ----------
if (-not $NoConfig -and $Role -ne 'none') {
    if (-not (Test-Path $appcfg)) { throw "找不到 app 配置: $appcfg" }

    $lines = [System.Collections.Generic.List[string]](Get-Content $appcfg)

    function Set-Cfg([string]$key, $value) {
        $newLine = if ([string]::IsNullOrEmpty($value)) { "# $key is not set" } else { "$key=$value" }
        $pattern = '^\s*#?\s*' + [regex]::Escape($key) + '\s*(=|\s+is\s+not\s+set\s*$)'
        $idxs = @()
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match $pattern) { $idxs += $i }
        }
        if ($idxs.Count -gt 0) {
            $lines[$idxs[0]] = $newLine
            for ($k = $idxs.Count - 1; $k -ge 1; $k--) { $lines.RemoveAt($idxs[$k]) }
        } else {
            $lines.Add($newLine) | Out-Null
        }
    }

    Set-Cfg 'CONFIG_SAMPLE_ENABLE' 'y'
    Set-Cfg 'CONFIG_ENABLE_PRODUCTS_SAMPLE' 'y'
    Set-Cfg 'CONFIG_SAMPLE_SUPPORT_LINK_SCOPE' 'y'
    foreach ($other in 'CONFIG_SAMPLE_SUPPORT_SLE_UART', 'CONFIG_SAMPLE_SUPPORT_BLE_UART',
                       'CONFIG_SAMPLE_SUPPORT_SLE_UART_1_VS_8', 'CONFIG_SAMPLE_SUPPORT_SLE_GETAWAY',
                       'CONFIG_SAMPLE_SUPPORT_SLE_LEARNING_DEMO') {
        Set-Cfg $other $null
    }
    foreach ($r in 'INITIATOR', 'RESPONDER', 'LOOPBACK', 'TIMING', 'MONITOR') {
        if ($r -ieq $Role.ToUpper()) { Set-Cfg "CONFIG_LINK_SCOPE_ROLE_$r" 'y' } else { Set-Cfg "CONFIG_LINK_SCOPE_ROLE_$r" $null }
    }
    foreach ($k in 'SLE', 'BLE') {
        if ($k -ieq $Link.ToUpper()) { Set-Cfg "CONFIG_LINK_SCOPE_LINK_$k" 'y' } else { Set-Cfg "CONFIG_LINK_SCOPE_LINK_$k" $null }
    }
    if ($SleWriteMode -eq 'command') {
        Set-Cfg 'CONFIG_LINK_SCOPE_SLE_WRITE_REQUEST' $null
        Set-Cfg 'CONFIG_LINK_SCOPE_SLE_WRITE_COMMAND' 'y'
    } else {
        Set-Cfg 'CONFIG_LINK_SCOPE_SLE_WRITE_REQUEST' 'y'
        Set-Cfg 'CONFIG_LINK_SCOPE_SLE_WRITE_COMMAND' $null
    }
    if ($BleWriteMode -eq 'request') {
        Set-Cfg 'CONFIG_LINK_SCOPE_BLE_WRITE_REQUEST' 'y'
        Set-Cfg 'CONFIG_LINK_SCOPE_BLE_WRITE_COMMAND' $null
    } else {
        Set-Cfg 'CONFIG_LINK_SCOPE_BLE_WRITE_REQUEST' $null
        Set-Cfg 'CONFIG_LINK_SCOPE_BLE_WRITE_COMMAND' 'y'
    }
    Set-Cfg 'CONFIG_LINK_SCOPE_CFG_ID' '0'
    Set-Cfg 'CONFIG_LINK_SCOPE_PROBE_HZ' ([string]$Hz)
    Set-Cfg 'CONFIG_LINK_SCOPE_PAYLOAD_BYTES' ([string]$Payload)
    Set-Cfg 'CONFIG_LINK_SCOPE_TIMEOUT_MS' ([string]$TimeoutMs)
    Set-Cfg 'CONFIG_LINK_SCOPE_WINDOW' ([string]$Window)
    Set-Cfg 'CONFIG_LINK_SCOPE_INTV_SLOT' ([string]$IntvSlot)
    Set-Cfg 'CONFIG_LINK_SCOPE_BLE_INTV_UNIT' ([string]$BleIntvUnit)
    Set-Cfg 'CONFIG_LINK_SCOPE_LOOP_US' '500'
    Set-Cfg 'CONFIG_LINK_SCOPE_TASK_PRIO' '28'
    if ($Quiet) {
        Set-Cfg 'CONFIG_LINK_SCOPE_RAW_PRINT' $null
    } else {
        Set-Cfg 'CONFIG_LINK_SCOPE_RAW_PRINT' 'y'
    }
    Set-Cfg 'CONFIG_LINK_SCOPE_STATS_EVERY' '200'
    Set-Cfg 'CONFIG_LINK_SCOPE_RUN_SELFTEST' 'y'
    Set-Cfg 'CONFIG_LINK_SCOPE_TIMING_GPIO' '2'
    Set-Cfg 'CONFIG_LINK_SCOPE_MONITOR_SEEK_INTERVAL' '100'
    Set-Cfg 'CONFIG_LINK_SCOPE_MONITOR_SEEK_WINDOW' '100'
    Set-Cfg 'CONFIG_LINK_SCOPE_MONITOR_FILTER_DUP' $null

    [System.IO.File]::Copy($appcfg, "$appcfg.bak.linkscope", $true)
    Write-TextLf $appcfg ($lines -join "`n")
    Write-Host "[4/4] .config 已切到 role=$Role link=$Link sle-write=$SleWriteMode ble-write=$BleWriteMode ble-intv=$BleIntvUnit（备份 $appcfg.bak.linkscope）" -ForegroundColor Green
}

Write-Host ""
Write-Host "下一步（在 Ubuntu 虚拟机里）：" -ForegroundColor Cyan
Write-Host "  cd /home/bearpi/project/bearpi-pico_h3863-master"
Write-Host "  python3 build.py -c ws63-liteos-app 2>&1 | tee linkscope_$Role.log"
Write-Host "  产物: output/ws63/fwpkg/ws63-liteos-app/ws63-liteos-app_all.fwpkg"

