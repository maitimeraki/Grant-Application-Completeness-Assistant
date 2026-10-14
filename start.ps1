# One-command dev start for the Grant Application Completeness Assistant.
# Usage: .\start.ps1   (from the project root)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

# --- Backend: venv + deps ---
$venvPy = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "Creating Python venv..."
    python -m venv (Join-Path $root "backend\.venv")
}
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet -r (Join-Path $root "backend\requirements.txt")

if (-not (Test-Path (Join-Path $root "backend\.env"))) {
    Copy-Item (Join-Path $root "backend\.env.example") (Join-Path $root "backend\.env")
    Write-Host "Created backend\.env from .env.example (add your LLM API key there)."
}

# --- Frontend: node_modules ---
Push-Location (Join-Path $root "frontend")
if (-not (Test-Path "node_modules")) {
    Write-Host "Installing frontend dependencies..."
    npm install
}
Pop-Location

# --- Launch both services in separate windows ---
Start-Process pwsh -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$root\backend'; & '.\.venv\Scripts\Activate.ps1'; uvicorn app.main:app --reload --host 127.0.0.1 --port 8000"
)
Start-Process pwsh -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$root\frontend'; npm run dev"
)

Write-Host ""
Write-Host "Backend  : http://127.0.0.1:8000  (API docs at /docs)"
Write-Host "Frontend : http://localhost:5173"
