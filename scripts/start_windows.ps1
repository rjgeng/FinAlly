param(
    [switch]$Build
)

$ErrorActionPreference = "Stop"

$ContainerName = "finally"
$ImageName     = "finally"
$DataVolume    = "finally-data"

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

# Build image if it doesn't exist or -Build was passed
$imageExists = $true
try {
    docker image inspect $ImageName *> $null
    if ($LASTEXITCODE -ne 0) { $imageExists = $false }
} catch {
    $imageExists = $false
}

if ($Build -or -not $imageExists) {
    Write-Host "Building FinAlly image..."
    docker build -t $ImageName $ProjectRoot
    if ($LASTEXITCODE -ne 0) { throw "docker build failed" }
}

# Stop existing container if running
$running = docker ps -q --filter "name=^$ContainerName$"
if ($running) {
    Write-Host "Container already running. Stopping first..."
    docker stop $ContainerName | Out-Null
    docker rm $ContainerName | Out-Null
}

# Remove stopped container with same name if it exists
$existing = docker ps -aq --filter "name=^$ContainerName$"
if ($existing) {
    docker rm $ContainerName | Out-Null
}

# Run the container
docker run -d `
    --name $ContainerName `
    -v "${DataVolume}:/app/db" `
    -p 8000:8000 `
    --env-file "$ProjectRoot\.env" `
    $ImageName | Out-Null

if ($LASTEXITCODE -ne 0) { throw "docker run failed" }

Write-Host ""
Write-Host "FinAlly is running at http://localhost:8000"
Write-Host "Stop with: .\scripts\stop_windows.ps1"
