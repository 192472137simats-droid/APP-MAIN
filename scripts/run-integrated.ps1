[CmdletBinding()]
param(
    [int]$AppPort = 8000,
    [int]$CorePort = 8081
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$buildDir = Join-Path $root 'cpp\build\integrated'
$server = Join-Path $buildDir 'research-advisor-server.exe'
$staticDir = Join-Path $root 'cpp\web'
$dataDir = Join-Path $root 'data'
$python = Join-Path $root 'venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    $python = Join-Path $root '.venv\Scripts\python.exe'
}
if (-not (Test-Path $python)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}

Push-Location $root
$previousCoreUrl = $env:CPP_CORE_URL
$coreProcess = $null
try {
    cmake -S cpp -B $buildDir -G Ninja
    if ($LASTEXITCODE -ne 0) { throw 'C++ core configuration failed.' }

    cmake --build $buildDir --config Release
    if ($LASTEXITCODE -ne 0) { throw 'C++ core build failed.' }

    $probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $CorePort)
    try {
        $probe.Start()
    } catch {
        throw "Port $CorePort is already in use. Stop that process, then retry."
    } finally {
        $probe.Stop()
    }

    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $stdoutLog = Join-Path $dataDir "cpp-core-$stamp.out.log"
    $stderrLog = Join-Path $dataDir "cpp-core-$stamp.err.log"
    $arguments = "$CorePort `"$staticDir`""
    $coreProcess = Start-Process `
        -FilePath $server `
        -ArgumentList $arguments `
        -WorkingDirectory $root `
        -PassThru `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog

    $coreReady = $false
    $coreFailure = $null
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if ($coreProcess.HasExited) { break }
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$CorePort/api/health" -TimeoutSec 2
            if ($health.service -eq 'research-advisor-cpp') {
                if (-not $health.analysis_configured) {
                    $coreFailure = 'C++ core is running, but LLM_API_KEY or LLM_MODEL is not configured.'
                } else {
                    $coreReady = $true
                }
                break
            }
        } catch {
            Start-Sleep -Milliseconds 250
        }
    }
    if (-not $coreReady) {
        if ($null -ne $coreFailure) { throw $coreFailure }
        $details = if (Test-Path $stderrLog) { Get-Content -Raw $stderrLog } else { '' }
        throw "C++ core did not start. $details"
    }

    $env:CPP_CORE_URL = "http://127.0.0.1:$CorePort"
    Write-Host "ResearchAdvisorLM: http://127.0.0.1:$AppPort"
    Write-Host "C++ analysis core: http://127.0.0.1:$CorePort (loopback only)"
    Write-Host 'Press Ctrl+C to stop the app and its C++ core.'
    & $python -m uvicorn app.main:app --host 127.0.0.1 --port $AppPort
    if ($LASTEXITCODE -ne 0) { throw 'The FastAPI app exited with an error.' }
} finally {
    if ($null -eq $previousCoreUrl) {
        Remove-Item Env:CPP_CORE_URL -ErrorAction SilentlyContinue
    } else {
        $env:CPP_CORE_URL = $previousCoreUrl
    }
    if ($null -ne $coreProcess -and -not $coreProcess.HasExited) {
        Stop-Process -Id $coreProcess.Id -Force
    }
    Pop-Location
}
