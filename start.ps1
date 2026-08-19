$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    $secret = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLowerInvariant()
    $dbPassword = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24)).ToLowerInvariant()
    $appDbPassword = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(24)).ToLowerInvariant()
    $content = Get-Content ".env" -Raw
    $content = $content.Replace("CHANGE_ME_generate_a_long_random_value", $secret).Replace("CHANGE_ME_database_password", $dbPassword).Replace("CHANGE_ME_application_database_password", $appDbPassword)
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot ".env"), $content)
    Write-Host "Created .env with generated secrets."
}

@("attachments", "previews", "exports", "backups") | ForEach-Object {
    New-Item -ItemType Directory -Force -Path (Join-Path "workledger-data" $_) | Out-Null
}
docker compose up --build --detach
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
docker compose ps
Write-Host "WorkLedger: http://127.0.0.1:8787"
