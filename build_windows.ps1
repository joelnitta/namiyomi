param(
    [string]$Python = "python",
    [string]$OutputDirectory = "release"
)

# Run from a prepared Python 3.12 x64 environment on Windows. This script does
# not install dependencies, modify execution policy, or request administrator access.
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$sourceDirectory = $PSScriptRoot
$buildTemporary = $null
$oldRecoveryDirectory = $env:NAMIYOMI_RECOVERY_DIR
$oldPreferencesPath = $env:NAMIYOMI_PREFERENCES_PATH

function Invoke-Python {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Python command failed (exit $LASTEXITCODE): $Arguments" }
}

Push-Location $sourceDirectory
try {
    Invoke-Python -c "import platform,sys; assert sys.platform=='win32' and platform.machine().upper() in ('AMD64','X86_64') and sys.maxsize>2**32, 'Windows x64 Python is required'; assert sys.version_info[:2]==(3,12), 'Use Python 3.12 for this release'"
    if (-not (Test-Path -LiteralPath (Join-Path $sourceDirectory "assets/namiyomi.ico"))) {
        throw "The Windows icon is missing."
    }
    $version = (& $Python -c "from app_info import APP_VERSION; print(APP_VERSION)").Trim()
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '^\d+\.\d+\.\d+$') { throw "Invalid version" }
    $outputPath = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
        [System.IO.Path]::GetFullPath($OutputDirectory)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $sourceDirectory $OutputDirectory))
    }
    [System.IO.Directory]::CreateDirectory($outputPath) | Out-Null
    $zipPath = Join-Path $outputPath "namiyomi-$version-Windows-x64.zip"
    if (Test-Path -LiteralPath $zipPath) { throw "Refusing to replace an existing release: $zipPath" }
    $buildTemporary = Join-Path ([System.IO.Path]::GetTempPath()) ("namiyomi-windows-" + [guid]::NewGuid().ToString("N"))
    [System.IO.Directory]::CreateDirectory($buildTemporary) | Out-Null
    $env:NAMIYOMI_RECOVERY_DIR = Join-Path $buildTemporary "Recovery"
    $env:NAMIYOMI_PREFERENCES_PATH = Join-Path $buildTemporary "preferences.json"
    Invoke-Python -m unittest discover -q
    $sourceReport = Join-Path $buildTemporary "source-smoke.json"
    Invoke-Python app.py --smoke-test $sourceReport
    Invoke-Python -m PyInstaller --noconfirm --clean --distpath (Join-Path $buildTemporary "dist") --workpath (Join-Path $buildTemporary "work") namiyomi-windows.spec
    $applicationDirectory = Join-Path $buildTemporary "dist/namiyomi"
    Invoke-Python collect_licenses.py $applicationDirectory
    $stageDirectory = Join-Path $buildTemporary "package/namiyomi"
    [System.IO.Directory]::CreateDirectory((Split-Path $stageDirectory -Parent)) | Out-Null
    Copy-Item -LiteralPath $applicationDirectory -Destination $stageDirectory -Recurse
    foreach ($notice in @("LICENSE.txt", "CITATION.cff", "CITATION.txt")) {
        Copy-Item -LiteralPath (Join-Path $sourceDirectory $notice) -Destination $stageDirectory
    }
    Copy-Item -LiteralPath (Join-Path $sourceDirectory "docs/START_HERE-Windows.txt") -Destination (Join-Path $stageDirectory "START_HERE.txt")
    $demoDirectory = Join-Path $stageDirectory "demo"
    [System.IO.Directory]::CreateDirectory($demoDirectory) | Out-Null
    foreach ($sample in @("Demo01_F.ab1", "Demo01_R.ab1", "Demo02_F.ab1", "Demo02_R.ab1", "Demo.json", "README.md")) {
        Copy-Item -LiteralPath (Join-Path $sourceDirectory "demo/$sample") -Destination $demoDirectory
    }
    $stagedZipPath = Join-Path $buildTemporary ([System.IO.Path]::GetFileName($zipPath))
    Compress-Archive -LiteralPath $stageDirectory -DestinationPath $stagedZipPath -CompressionLevel Optimal
    # Verify the actual extracted ZIP, not just the pre-archive build tree.
    $verifyDirectory = Join-Path $buildTemporary "verify"
    Expand-Archive -LiteralPath $stagedZipPath -DestinationPath $verifyDirectory
    $executable = Join-Path $verifyDirectory "namiyomi/namiyomi.exe"
    if (-not (Test-Path -LiteralPath $executable)) { throw "The ZIP contains no executable." }
    $binaryReport = Join-Path $buildTemporary "binary-smoke.json"
    $argumentLine = '--smoke-test "' + $binaryReport + '"'
    $process = Start-Process -FilePath $executable -ArgumentList $argumentLine -WorkingDirectory (Split-Path $executable -Parent) -PassThru
    if (-not $process.WaitForExit(60000)) {
        $process.Kill()
        throw "The extracted executable did not finish its startup check."
    }
    if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $binaryReport)) {
        throw "The extracted executable startup check failed."
    }
    $report = Get-Content -LiteralPath $binaryReport -Raw | ConvertFrom-Json
    if ($report.status -ne "passed" -or $report.windowing_system -ne "win32") {
        throw "The extracted executable returned an invalid startup report."
    }
    Move-Item -LiteralPath $stagedZipPath -Destination $zipPath
    Copy-Item -LiteralPath $sourceReport -Destination (Join-Path $outputPath "windows-source-smoke.json")
    Copy-Item -LiteralPath $binaryReport -Destination (Join-Path $outputPath "windows-binary-smoke.json")
    $hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    [System.IO.File]::WriteAllText(($zipPath + ".sha256"), ($hash + "  " + [System.IO.Path]::GetFileName($zipPath) + "`n"), [System.Text.UTF8Encoding]::new($false))
    Write-Output "Built and checked: $zipPath"
    Write-Output "SHA256: $hash"
} finally {
    $env:NAMIYOMI_RECOVERY_DIR = $oldRecoveryDirectory
    $env:NAMIYOMI_PREFERENCES_PATH = $oldPreferencesPath
    Pop-Location
    # Delete only this invocation's UUID-named temporary directory. Never a
    # project, source directory, user data directory, or previous release.
    if ($buildTemporary -and ([System.IO.Path]::GetFileName($buildTemporary) -match '^namiyomi-windows-[0-9a-f]{32}$')) {
        Remove-Item -LiteralPath $buildTemporary -Recurse -Force
    }
}
