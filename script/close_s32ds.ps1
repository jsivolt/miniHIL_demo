# Closes running S32 Design Studio for S32 Platform 3.6.10 instances.
# Process name confirmed on this machine: s32ds (C:\NXP\S32DS.3.6.10\eclipse\s32ds.exe)

$processName = "s32ds"
$graceSeconds = 10

$procs = Get-Process -Name $processName -ErrorAction SilentlyContinue
if (-not $procs) {
    Write-Host "No running '$processName' process found."
    exit 0
}

foreach ($p in $procs) {
    Write-Host "Requesting graceful close of PID $($p.Id) ($($p.Path))..."
    $p.CloseMainWindow() | Out-Null
}

$deadline = (Get-Date).AddSeconds($graceSeconds)
while ((Get-Date) -lt $deadline) {
    $stillRunning = Get-Process -Name $processName -ErrorAction SilentlyContinue
    if (-not $stillRunning) {
        Write-Host "S32 Design Studio closed."
        exit 0
    }
    Start-Sleep -Milliseconds 500
}

$stillRunning = Get-Process -Name $processName -ErrorAction SilentlyContinue
if ($stillRunning) {
    Write-Host "Graceful close timed out, force killing remaining processes..."
    try {
        $stillRunning | Stop-Process -Force -ErrorAction Stop
        Write-Host "S32 Design Studio force closed."
    } catch {
        $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        if (-not $isAdmin) {
            # Access denied usually means the process is owned by another account/session; retry elevated.
            Write-Host "Access denied - relaunching this script with administrator privileges..."
            Start-Process -FilePath "powershell" -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs -Wait
        } else {
            Write-Host "Failed to stop process even with administrator rights: $($_.Exception.Message)"
            exit 1
        }
    }
} else {
    Write-Host "S32 Design Studio closed."
}
