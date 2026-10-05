$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$logPath = Join-Path $projectRoot 'artifacts/otel_demo/local_migration/wsl_features.txt'
Start-Transcript -Path $logPath -Append
try {
    foreach ($feature in @('VirtualMachinePlatform', 'Microsoft-Windows-Subsystem-Linux')) {
        Enable-WindowsOptionalFeature -Online -FeatureName $feature -All -NoRestart
    }
    & "$env:LOCALAPPDATA\Microsoft\WindowsApps\winget.exe" install --id Microsoft.WSL --exact --source winget --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
    Write-Output "WSL package install exit code: $LASTEXITCODE"
} finally {
    Stop-Transcript
}
