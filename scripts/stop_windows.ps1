$ErrorActionPreference = "Stop"

$ContainerName = "finally"

$running = docker ps -q --filter "name=^$ContainerName$"
if ($running) {
    Write-Host "Stopping FinAlly..."
    docker stop $ContainerName | Out-Null
    docker rm $ContainerName | Out-Null
    Write-Host "Stopped. Data volume preserved."
} else {
    $existing = docker ps -aq --filter "name=^$ContainerName$"
    if ($existing) {
        Write-Host "Removing stopped FinAlly container..."
        docker rm $ContainerName | Out-Null
        Write-Host "Removed. Data volume preserved."
    } else {
        Write-Host "FinAlly is not running."
    }
}
