$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$artifactDirectory = Join-Path $projectRoot 'artifacts/otel_demo/local_migration'
$features = foreach ($name in @('VirtualMachinePlatform', 'Microsoft-Windows-Subsystem-Linux')) {
    $feature = Get-WindowsOptionalFeature -Online -FeatureName $name
    [ordered]@{name=$name; state=$feature.State.ToString()}
}
$bootConfiguration = (& "$env:SystemRoot\System32\bcdedit.exe" /enum 2>&1 | Out-String)
$bootExit = $LASTEXITCODE
$bootConfiguration | Set-Content -Encoding UTF8 (Join-Path $artifactDirectory 'boot_configuration.txt')
$bootFixed = $false
if ($bootExit -eq 0 -and $bootConfiguration -match 'hypervisorlaunchtype\s+Off') {
    & "$env:SystemRoot\System32\bcdedit.exe" /set hypervisorlaunchtype Auto
    if ($LASTEXITCODE -ne 0) { throw 'Failed to enable hypervisor launch.' }
    $bootFixed = $true
}
$result = [ordered]@{
    features=$features
    hypervisor_present=(Get-CimInstance Win32_ComputerSystem).HypervisorPresent
    firmware_virtualization=(Get-CimInstance Win32_Processor | Select-Object -First 1).VirtualizationFirmwareEnabled
    last_boot=(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('o')
    boot_configuration_exit=$bootExit
    hypervisor_launch_fixed=$bootFixed
    servicing_reboot_pending=(Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending')
}
$result | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 (Join-Path $artifactDirectory 'virtualization_diagnosis.json')
