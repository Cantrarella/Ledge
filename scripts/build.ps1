# -*- coding: utf-8 -*-
<#
.SYNOPSIS
    Ledge 便携版 onedir 打包脚本（生成 dist/Ledge 便携目录、dist/Ledge-portable.zip 并同步根目录 Ledge.exe）
#>
param(
    [switch]$SkipZip = $false,
    [switch]$SkipInstaller = $false,
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
$StateBackupDir = Join-Path $env:TEMP ("Ledge-build-state-" + [guid]::NewGuid().ToString("N"))
$HasPortableConfig = Test-Path (Join-Path $PortableDir "config.json")
$HasPortableData = Test-Path (Join-Path $PortableDir "data")
New-Item -ItemType Directory -Force $StateBackupDir | Out-Null
if ($HasPortableConfig) {
    Copy-Item -Force (Join-Path $PortableDir "config.json") (Join-Path $StateBackupDir "config.json")
}
if ($HasPortableData) {
    Copy-Item -Recurse -Force (Join-Path $PortableDir "data") (Join-Path $StateBackupDir "data")
}

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
    Write-Host "[4/5] 生成便携版压缩包与 SHA256 校验文件: $ZipPath ..." -ForegroundColor Cyan
    if (Test-Path $ZipPath) {
        Remove-Item -Force $ZipPath
    }
    Compress-Archive -Path "$PortableDir\*" -DestinationPath $ZipPath -CompressionLevel Optimal
    $HashObj = Get-FileHash -Path $ZipPath -Algorithm SHA256
    $ShaPath = "$ZipPath.sha256"
    "$($HashObj.Hash.ToLowerInvariant())  $(Split-Path -Leaf $ZipPath)" | Set-Content -Path $ShaPath -Encoding ASCII
}

if (-not $SkipInstaller) {
    $IsccCandidates = @(
        "C:\Users\User\AppData\Local\Programs\Inno Setup 6\ISCC.exe",
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    )
    $IsccExe = $IsccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($IsccExe) {
        $AppVer = (python -c "import sys; sys.path.insert(0, r'$RepoRoot'); from wisland.core.branding import APP_VERSION; print(APP_VERSION)" 2>$null).Trim()
        if (-not $AppVer) { $AppVer = "1.0.0" }
        $IssPath = Join-Path $PSScriptRoot "Ledge.iss"
        Write-Host "[5/5] 生成安装版程序 (Inno Setup): Ledge-Setup-v$AppVer.exe ..." -ForegroundColor Cyan
        & $IsccExe "/DMyAppVersion=$AppVer" "/DSourceDir=$PortableDir" "/DOutputDir=$DistDir" $IssPath
        if ($LASTEXITCODE -ne 0) {
            throw "Inno Setup 打包失败 (ExitCode: $LASTEXITCODE)"
        }
        $SetupPath = Join-Path $DistDir "Ledge-Setup-v$AppVer.exe"
        if (Test-Path $SetupPath) {
            $SetupHash = Get-FileHash -Path $SetupPath -Algorithm SHA256
            "$($SetupHash.Hash.ToLowerInvariant())  $(Split-Path -Leaf $SetupPath)" | Set-Content -Path "$SetupPath.sha256" -Encoding ASCII
        }
    } else {
        Write-Host "[5/5] 未检测到 Inno Setup (ISCC.exe)，跳过安装版生成。" -ForegroundColor Yellow
    }
}

# 打包压缩包必须保持无个人数据；仅在最终重建的本地目录中恢复原配置。
if ($HasPortableConfig) {
    Copy-Item -Force (Join-Path $StateBackupDir "config.json") (Join-Path $PortableDir "config.json")
}
if ($HasPortableData) {
    Copy-Item -Recurse -Force (Join-Path $StateBackupDir "data\*") (Join-Path $PortableDir "data")
}
Remove-Item -Recurse -Force $StateBackupDir

if ($RestartApp) {
    Write-Host "正在启动 Ledge.exe..." -ForegroundColor Green
    Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = (Join-Path $RepoRoot "Ledge.exe")
        CurrentDirectory = $RepoRoot
    } | Out-Null
}

Write-Host "打包完成！" -ForegroundColor Green
