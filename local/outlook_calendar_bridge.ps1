# Outlook -> WorkBuddy calendar bridge
# Reads today + tomorrow appointments from local Outlook client,
# pushes them to the workbench API. Designed for Windows Task Scheduler (hourly).
# Requirements: Outlook desktop client installed and configured on this machine.
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$WB_URL   = 'https://xgrow.me/api/hrd/calendar/sync'
$SECRET   = 'hrd-workbench-sync-v1:Bohai228'
$DAYS_AHEAD = 1   # today + tomorrow

# --- derive token (same algorithm as workbench login token) ---
$sha = [System.Security.Cryptography.SHA256]::Create()
$tok = -join ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($SECRET)) | ForEach-Object { $_.ToString('x2') })

function Log($msg) {
    $line = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '  ' + $msg
    Write-Output $line
    $logDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    Add-Content -Path (Join-Path $logDir 'outlook_bridge.log') -Value $line -Encoding UTF8
}

try {
    Log '=== bridge start ==='
    $outlook = New-Object -ComObject Outlook.Application
    $ns  = $outlook.GetNamespace('MAPI')
    $cal = $ns.GetDefaultFolder(9)   # 9 = olFolderCalendar
    $items = $cal.Items
    $items.Sort('[Start]', $false)
    $items.IncludeRecurrences = $true   # expand recurring series into instances

    $start = (Get-Date).Date
    $end   = (Get-Date).Date.AddDays($DAYS_AHEAD + 1)
    $filter = "[Start] >= '{0}' AND [Start] < '{1}'" -f $start.ToString('g'), $end.ToString('g')
    $restricted = $items.Restrict($filter)

    $events = New-Object System.Collections.Generic.List[object]
    $count = 0
    foreach ($e in $restricted) {
        $count++
        if ($count -gt 200) { Log 'warn: more than 200 items, truncated'; break }
        $s = $e.Start
        $en = $e.End
        $uid = ('{0}|{1:yyyy-MM-dd}|{1:HHmm}' -f $e.EntryID, $s)
        $events.Add([ordered]@{
            uid      = $uid
            date     = $s.ToString('yyyy-MM-dd')
            start    = $s.ToString('HH:mm')
            end      = $en.ToString('HH:mm')
            subject  = [string]$e.Subject
            location = [string]$e.Location
            all_day  = [bool]$e.AllDayEvent
        })
    }
    Log ("collected {0} appointment instances ({1:yyyy-MM-dd} ~ {2:yyyy-MM-dd})" -f $events.Count, $start, $end.AddDays(-1))

    if ($events.Count -eq 0) {
        Log 'nothing to sync'
        Log '=== bridge end (ok) ==='
        exit 0
    }

    $body = @{ events = $events } | ConvertTo-Json -Depth 3
    $resp = Invoke-RestMethod -Uri $WB_URL -Method Post `
        -Headers @{ 'X-HRD-Token' = $tok } `
        -ContentType 'application/json; charset=utf-8' `
        -Body $body -TimeoutSec 30
    Log ("sync response: ok={0} dates={1} count={2}" -f $resp.ok, ($resp.dates -join ','), $resp.count)
    Log '=== bridge end (ok) ==='
}
catch {
    Log ('ERROR: ' + $_.Exception.Message)
    Log '=== bridge end (failed) ==='
    exit 1
}
