<#
.SYNOPSIS
    Windows 上跑 LinkScope 的主机侧单元测试。

.DESCRIPTION
    不需要装完整工具链：有 zig 就用 zig cc，没有就找 gcc/clang。
    zig 可以用 pip 装到项目内（不污染系统）：
        python -m pip install --target .tools\py ziglang
        $env:PYTHONPATH = "E:\nearlink-scope\.tools\py"
#>
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSCommandPath -Parent)

$out = (Join-Path (Resolve-Path '..\..\..\.tools\build\host-test') '').TrimEnd('\', '/')
New-Item -ItemType Directory -Force -Path $out | Out-Null

function Get-Cc {
    if (Get-Command gcc -ErrorAction SilentlyContinue) { return @('gcc') }
    if (Get-Command clang -ErrorAction SilentlyContinue) { return @('clang') }
    $env:ZIG_GLOBAL_CACHE_DIR = Join-Path $out 'zig-cache'
    $env:ZIG_LOCAL_CACHE_DIR  = Join-Path $out 'zig-cache'
    # zig 只在最后一步才碰 TMPDIR；系统 TEMP 在沙箱里不可写时会报
    # "unable to make temporary file: Permission denied"，所以统一指到工作区
    $tmp = Join-Path $out 'tmp'
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    $env:TMPDIR = $tmp; $env:TMP = $tmp; $env:TEMP = $tmp
    try { python -m ziglang version | Out-Null; return @('python', '-m', 'ziglang', 'cc') } catch { }
    throw '没有找到 C 编译器：装 gcc/clang，或 pip install --target .tools\py ziglang 后设置 PYTHONPATH'
}

$cc = Get-Cc
$common = @('-std=c99', '-Wall', '-Wextra')

Write-Host '== 1/2 probe + stats 自检 ==' -ForegroundColor Cyan
& $cc[0] @($cc[1..($cc.Count-1)]) @common '-DLS_HOST_TEST' '-I..' '../selftest.c' '../probe.c' '../stats.c' 'host_main.c' '-o' "$out/ls_selftest.exe"
& "$out/ls_selftest.exe"
if ($LASTEXITCODE -ne 0) { throw "selftest failed ($LASTEXITCODE)" }

Write-Host '== 2/2 engine + loopback 集成测试 ==' -ForegroundColor Cyan
& $cc[0] @($cc[1..($cc.Count-1)]) @common '-Ishim' '-I..' '../engine.c' '../link_loopback.c' '../probe.c' '../stats.c' 'host_engine_test.c' '-o' "$out/ls_engine_test.exe"
& "$out/ls_engine_test.exe"
if ($LASTEXITCODE -ne 0) { throw "engine test failed ($LASTEXITCODE)" }

Write-Host 'host tests passed' -ForegroundColor Green
