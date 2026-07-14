$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

$activate = Join-Path $PSScriptRoot "venv\Scripts\Activate.ps1"
if (-not (Test-Path -LiteralPath $activate)) {
    throw "Virtual environment not found. Run: python -m venv backend\venv"
}

. $activate

$envFile = Join-Path (Split-Path $PSScriptRoot) ".env.local"
if (Test-Path -LiteralPath $envFile) {
    $lineNumber = 0
    foreach ($line in Get-Content -LiteralPath $envFile) {
        $lineNumber++
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }

        $name, $value = $trimmed -split "=", 2
        if (-not $name -or $null -eq $value) {
            throw "Invalid environment entry at .env.local line $lineNumber."
        }

        $name = $name.Trim()
        if ($name -notmatch "^[A-Za-z_][A-Za-z0-9_]*$") {
            throw "Invalid environment variable name at .env.local line $lineNumber."
        }

        $value = $value.Trim()
        if (
            $value.Length -ge 2 -and (
                ($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'"))
            )
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
