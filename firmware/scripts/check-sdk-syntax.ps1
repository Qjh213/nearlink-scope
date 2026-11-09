<#
.SYNOPSIS
    用 zig cc + SDK 真头文件，给 linkscope 的每个 .c 做语法/类型检查（不链接、不烧板）。

.DESCRIPTION
    Windows 上没有 RISC-V 工具链，也不该为了"看看能不能编过"去装一套。
    这个脚本只做一件事：把所有 .c 用 zig cc -c 编成 .o，头文件用 SDK 里的真头文件
    （不是自己写的 shim），所以能抓到"函数名拼错 / 参数类型不对 / 宏不存在"这类问题。

    注意：zig 的 -fsyntax-only 在本机会报 FileNotFound，必须用 -c -o。

    用法：
        .\check-sdk-syntax.ps1                       # 默认检查 Z: 上的活动 SDK
        .\check-sdk-syntax.ps1 -SdkRoot 'E:\...'     # 换一份 SDK
#>
param(
    [string]$SdkRoot = 'Z:\home\bearpi\project\bearpi-pico_h3863-master',
    [string]$SrcDir  = (Join-Path (Split-Path $PSCommandPath -Parent) '..\linkscope'),
    [ValidateSet('request', 'command')]
    [string]$SleWriteMode = 'request'
)

$ErrorActionPreference = 'Stop'
$SrcDir = (Resolve-Path $SrcDir).Path

# zig 是 pip --target 装在工作区里的（.tools\py），不设 PYTHONPATH 就找不到模块
$pyDir = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\.tools\py'))
if (Test-Path $pyDir) { $env:PYTHONPATH = $pyDir }

$rel = @(
    'include',
    'include\driver',
    'include\middleware\services\bts\sle',
    'include\middleware\services\bts\ble',
    'include\middleware\services\bts\common',
    'kernel\osal\include',
    'kernel\osal\include\debug',
    'kernel\osal\include\memory',
    'kernel\osal\include\schedule',
    'kernel\osal\include\soc_osal',
    'open_source\libboundscheck\include',
    'middleware\utils\app_init',
    'middleware\utils\common_headers',
    'drivers\chips\ws63\include',
    'drivers\drivers\hal\gpio',
    'drivers\drivers\hal\gpio\v150',
    'drivers\drivers\hal\pinmux\common',
    'drivers\chips\ws63\porting\gpio',
    'drivers\chips\ws63\porting\pinctrl'
    'drivers\boards\ws63\evb\memory_config\include\product'
)

$incs = @()
foreach ($r in $rel) {
    $p = Join-Path $SdkRoot $r
    if (Test-Path $p) { $incs += "-I$p" } else { Write-Host "  [warn] 缺头文件目录: $r" -ForegroundColor DarkYellow }
}

$defs = @('-DCHIP_WS63=1', '-DCHIP_VERSION=1', '-DBOARD_ASIC', '-DPRE_ASIC', '-DCORE=APPS')
if ($SleWriteMode -eq 'command') {
    $defs += '-DCONFIG_LINK_SCOPE_SLE_WRITE_COMMAND=1'
} else {
    $defs += '-DCONFIG_LINK_SCOPE_SLE_WRITE_REQUEST=1'
}
$flags = @('-std=c99', '-Wall', '-Wextra', '-c') + $defs + $incs

$out = Join-Path (Split-Path $PSCommandPath -Parent) '..\..\.tools\build\syntax'
New-Item -ItemType Directory -Force -Path $out | Out-Null
$env:ZIG_GLOBAL_CACHE_DIR = Join-Path $out 'zig-cache'
$env:ZIG_LOCAL_CACHE_DIR  = Join-Path $out 'zig-cache'

# 根目录是 LinkScope 逻辑；sle\ 下虽然源自官方样例，但服务属性已被 LinkScope 修改，
# 也必须纳入语法检查，避免只验证 initiator、漏掉 responder 构建错误。
$files = @(
    Get-Item (Join-Path $SrcDir '*.c')
    Get-Item (Join-Path $SrcDir 'sle\sle_uart_server.c')
    Get-Item (Join-Path $SrcDir 'sle\sle_uart_server_adv.c')
)
$fail = 0
foreach ($f in $files) {
    $o = Join-Path $out ($f.BaseName + '.o')
    $err = & python -m ziglang cc @flags $f.FullName '-o' $o 2>&1
    if ($LASTEXITCODE -ne 0) {
        $fail++
        Write-Host "FAIL $($f.Name)" -ForegroundColor Red
        $err | ForEach-Object { "  $_" }
    } else {
        # zig 会打印 clang 特有的告警（SDK 样例同样写法），只提示不失败
        $w = @($err | Where-Object { $_ -match 'warning:' })
        if ($w.Count -gt 0) {
            Write-Host "ok   $($f.Name)  ($($w.Count) warning)" -ForegroundColor Yellow
            $w | Select-Object -First 5 | ForEach-Object { "  $_" }
        } else {
            Write-Host "ok   $($f.Name)" -ForegroundColor Green
        }
    }
}

if ($fail -gt 0) { throw "$fail 个文件编不过" }
Write-Host "全部 $($files.Count) 个 .c 通过（真 SDK 头文件，SLE write=$SleWriteMode）" -ForegroundColor Green
