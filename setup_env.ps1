# GraphHarvestor - Environment Setup Script (PowerShell)
# Usage: .\setup_env.ps1

$ErrorActionPreference = "Stop"
$VENV_DIR = ".venv"

# 1. Check Python
Write-Host ""
Write-Host "[1/5] Checking Python installation..." -ForegroundColor Cyan
$pythonCmd = $null
foreach ($cmd in @("python", "python3")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -ge 3 -and $minor -ge 10) {
                $pythonCmd = $cmd
                Write-Host "  OK: Found $ver" -ForegroundColor Green
                break
            }
        }
    } catch {
        # command not found, try next
    }
}
if (-not $pythonCmd) {
    Write-Host "  ERROR: Python 3.10+ not found. Install from https://python.org" -ForegroundColor Red
    exit 1
}

# 2. Create virtual environment
Write-Host ""
Write-Host "[2/5] Creating virtual environment in '$VENV_DIR'..." -ForegroundColor Cyan
if (Test-Path $VENV_DIR) {
    Write-Host "  INFO: '$VENV_DIR' already exists - skipping creation." -ForegroundColor Yellow
} else {
    & $pythonCmd -m venv $VENV_DIR
    Write-Host "  OK: Virtual environment created." -ForegroundColor Green
}

# 3. Resolve pip path
$pip = Join-Path $VENV_DIR "Scripts\pip.exe"
if (-not (Test-Path $pip)) {
    Write-Host "  ERROR: pip not found at $pip" -ForegroundColor Red
    exit 1
}

# 4. Upgrade pip
Write-Host ""
Write-Host "[3/5] Upgrading pip..." -ForegroundColor Cyan
& $pip install --upgrade pip --quiet
Write-Host "  OK: pip upgraded." -ForegroundColor Green

# 5. Install dependencies
Write-Host ""
Write-Host "[4/5] Installing dependencies from requirements.txt..." -ForegroundColor Cyan
if (-not (Test-Path "requirements.txt")) {
    Write-Host "  ERROR: requirements.txt not found." -ForegroundColor Red
    exit 1
}
& $pip install -r requirements.txt
Write-Host "  OK: Dependencies installed." -ForegroundColor Green

# 6. Create .env if missing
Write-Host ""
Write-Host "[5/5] Checking .env file..." -ForegroundColor Cyan
if (-not (Test-Path ".env")) {
    $lines = @(
        "# GraphHarvestor - Environment Variables",
        "# Fill in your values below.",
        "",
        "# OpenRouter (https://openrouter.ai/keys)",
        "OPENROUTER_API_KEY=",
        "OPENROUTER_BASE_URL=https://openrouter.ai/api/v1",
        "",
        "# Groq (https://console.groq.com/keys)",
        "GROQ_API_KEY=",
        "",
        "# Google OAuth 2.0 - path to your downloaded client_secret_*.json",
        "GOOGLE_CREDENTIALS_JSON=credentials/google_client_secret.json",
        "",
        "# Neo4j (optional)",
        "NEO4J_URI=bolt://localhost:7687",
        "NEO4J_USERNAME=neo4j",
        "NEO4J_PASSWORD=",
        "",
        "LOG_LEVEL=INFO"
    )
    $lines | Set-Content ".env" -Encoding UTF8
    Write-Host "  OK: .env created. Fill in your API keys before running the pipeline." -ForegroundColor Green
} else {
    Write-Host "  INFO: .env already exists - skipping." -ForegroundColor Yellow
}

# Done
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Setup complete! Activate your environment with:" -ForegroundColor White
Write-Host "    .venv\Scripts\activate" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
