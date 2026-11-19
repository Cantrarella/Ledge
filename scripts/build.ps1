# -*- coding: utf-8 -*-
<#
.SYNOPSIS
    Ledge 便携版 onedir 打包脚本（生成 dist/Ledge 便携目录、dist/Ledge-portable.zip 并同步根目录 Ledge.exe）
#>
param(
    [switch]$SkipZip = $false,
    [switch]$RestartApp = $false
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$BuildEnvSite = "D:\Codex\wisland-exe\build\site-packages"
$WorkDir = "D:\Codex\wisland-exe\build\work"
$DistDir = Join-Path $RepoRoot "dist"
$PortableDir = Join-Path $DistDir "Ledge"
$ZipPath = Join-Path $DistDir "Ledge-portable.zip"
$PythonExe = "C:\Users\User\AppData\Local\Programs\Python\Python313\python.exe"

Write-Host "[1/4] 停止正在运行的 Ledge / WIsland 进程..." -ForegroundColor Cyan
Get-Process -Name "Ledge","WIsland" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 400

if (Test-Path "D:\Codex\wisland-exe\build\frozen\wisland") {
    Remove-Item -Recurse -Force "D:\Codex\wisland-exe\build\frozen\wisland\*"
    Copy-Item -Recurse -Force (Join-Path $RepoRoot "wisland\*") "D:\Codex\wisland-exe\build\frozen\wisland\"
}

Write-Host "[2/4] 运行 PyInstaller (onedir)..." -ForegroundColor Cyan
$env:PYTHONPATH = "$RepoRoot;$BuildEnvSite"
& $PythonExe -m PyInstaller --noconfirm --distpath $DistDir --workpath $WorkDir (Join-Path $RepoRoot "Ledge.spec")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller 打包失败 (ExitCode: $LASTEXITCODE)"
}

# 创建便携目录的 data 空文件夹（不包含大体量实时字幕模型与个人隐私配置）
New-Item -ItemType Directory -Force (Join-Path $PortableDir "data") | Out-Null

Write-Host "[3/4] 同步可执行文件至仓库根目录与 wisland-exe..." -ForegroundColor Cyan
Copy-Item -Force (Join-Path $PortableDir "Ledge.exe") (Join-Path $RepoRoot "Ledge.exe")
New-Item -ItemType Directory -Force (Join-Path $RepoRoot "_internal") | Out-Null
Copy-Item -Recurse -Force (Join-Path $PortableDir "_internal\*") (Join-Path $RepoRoot "_internal")

if (-not $SkipZip) {
    Write-Host "[4/4] 生成便携版压缩包: $ZipPath ..." -ForegroundColor Cyan
    if (Test-Path $ZipPath) {
        Remove-Item -Force $ZipPath
    }
    Compress-Archive -Path "$PortableDir\*" -DestinationPath $ZipPath -CompressionLevel Optimal
}

if ($RestartApp) {
    Write-Host "正在启动 Ledge.exe..." -ForegroundColor Green
    Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = (Join-Path $RepoRoot "Ledge.exe")
        CurrentDirectory = $RepoRoot
    } | Out-Null
}

Write-Host "打包完成！" -ForegroundColor Green
