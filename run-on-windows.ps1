$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location $root

$pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pythonLauncher) {
    & $pythonLauncher.Source -3.12 -c 'import sys; print(sys.version)' *> $null
    if ($LASTEXITCODE -eq 0) {
        & $pythonLauncher.Source -3.12 -m venv (Join-Path $root '.venv')
    } else {
        throw 'Python 3.12 is required. Install it from https://www.python.org/downloads/ and try again.'
    }
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw 'Python 3.12 is required. Install it from https://www.python.org/downloads/ and try again.'
    }
    & $pythonCommand.Source -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)'
    if ($LASTEXITCODE -ne 0) {
        throw 'Python 3.12 is required. Install it from https://www.python.org/downloads/ and try again.'
    }
    & $pythonCommand.Source -m venv (Join-Path $root '.venv')
}

$venvPython = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw 'Could not create the Python virtual environment.'
}

& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Could not update pip.' }
& $venvPython -m pip install -r (Join-Path $root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install project dependencies.' }

if ([string]::IsNullOrWhiteSpace($env:LLM_API_KEY)) {
    $secureKey = Read-Host 'Enter your OpenRouter (or compatible provider) API key' -AsSecureString
    $keyPointer = [IntPtr]::Zero
    try {
        $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
        $env:LLM_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
    } finally {
        if ($keyPointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
        }
        $secureKey.Dispose()
    }
}

if ([string]::IsNullOrWhiteSpace($env:LLM_API_KEY)) {
    throw 'An API key is required to run paper analysis.'
}

$env:CPP_CORE_URL = ''
$env:DATA_DIR = Join-Path $root 'data'
New-Item -ItemType Directory -Path $env:DATA_DIR -Force | Out-Null
Write-Host 'Starting ResearchAdvisorLM at http://127.0.0.1:8000'
Write-Host 'Press Ctrl+C to stop the app.'
& $venvPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000
if ($LASTEXITCODE -ne 0) { throw 'The app exited with an error.' }
