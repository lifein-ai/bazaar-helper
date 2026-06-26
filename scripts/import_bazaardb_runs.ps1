param(
    [int]$Limit = 20,
    [string]$ListUrl = "https://bazaardb.gg/run",
    [string]$OutputPath = "data\bazaardb_runs.json",
    [string]$BuildOutputPath = "data\bazaardb_builds.json",
    [int]$Port = 9222,
    [int]$PageWaitSeconds = 8
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

$EdgePathCandidates = @(
    "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
)
$EdgePath = $EdgePathCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $EdgePath) {
    throw "Microsoft Edge was not found."
}

$ProfilePath = Join-Path $ProjectRoot ".edge-bazaardb-profile"
New-Item -ItemType Directory -Force -Path $ProfilePath | Out-Null

function Test-DevTools {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/version" -TimeoutSec 1 | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

if (-not (Test-DevTools)) {
    Start-Process `
        -FilePath $EdgePath `
        -ArgumentList @(
            "--remote-debugging-port=$Port",
            "--user-data-dir=$ProfilePath",
            "--no-first-run",
            "--disable-default-apps",
            $ListUrl
        ) `
        -WorkingDirectory $ProjectRoot
    Start-Sleep -Seconds $PageWaitSeconds
}

function Receive-CdpMessage {
    $chunks = New-Object System.Collections.Generic.List[byte]
    do {
        $buffer = New-Object byte[] 65536
        $segment = [ArraySegment[byte]]::new($buffer)
        $result = $script:WebSocket.ReceiveAsync(
            $segment,
            [Threading.CancellationToken]::None
        ).Result
        for ($i = 0; $i -lt $result.Count; $i++) {
            $chunks.Add($buffer[$i])
        }
    } while (-not $result.EndOfMessage)

    return [Text.Encoding]::UTF8.GetString($chunks.ToArray())
}

function Send-Cdp {
    param(
        [string]$Method,
        [hashtable]$Params = @{}
    )

    $script:MessageId++
    $payload = @{
        id = $script:MessageId
        method = $Method
        params = $Params
    } | ConvertTo-Json -Depth 50 -Compress

    $bytes = [Text.Encoding]::UTF8.GetBytes($payload)
    $script:WebSocket.SendAsync(
        [ArraySegment[byte]]::new($bytes),
        [System.Net.WebSockets.WebSocketMessageType]::Text,
        $true,
        [Threading.CancellationToken]::None
    ).Wait()

    while ($true) {
        $message = Receive-CdpMessage | ConvertFrom-Json
        if ($message.id -eq $script:MessageId) {
            return $message
        }
    }
}

function Invoke-BrowserEval {
    param([string]$Expression)

    $result = Send-Cdp `
        -Method "Runtime.evaluate" `
        -Params @{
            expression = $Expression
            returnByValue = $true
            awaitPromise = $true
        }
    return $result.result.result.value
}

function Connect-BazaarDbTab {
    $tabs = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json"
    $tab = $tabs |
        Where-Object { $_.type -eq "page" -and $_.url -like "https://bazaardb.gg*" } |
        Select-Object -First 1

    if (-not $tab) {
        $newTab = Invoke-RestMethod -Method Put -Uri "http://127.0.0.1:$Port/json/new?$ListUrl"
        $tab = $newTab
    }

    $script:WebSocket = [System.Net.WebSockets.ClientWebSocket]::new()
    $script:WebSocket.ConnectAsync(
        [Uri]$tab.webSocketDebuggerUrl,
        [Threading.CancellationToken]::None
    ).Wait()
    $script:MessageId = 0

    Send-Cdp -Method "Page.enable" | Out-Null
    Send-Cdp -Method "Runtime.enable" | Out-Null
}

function Navigate-And-Wait {
    param([string]$Url)

    Send-Cdp -Method "Page.navigate" -Params @{ url = $Url } | Out-Null
    Start-Sleep -Seconds $PageWaitSeconds
}

function Get-RunList {
    $expression = @'
(() => {
  const ids = Array.from(document.querySelectorAll('a[href*="/run/"]'))
    .map(a => a.href.match(/\/run\/([0-9a-f-]{36})/)?.[1])
    .filter(Boolean);
  return [...new Set(ids)].slice(0, 200).map(id => ({
    id,
    url: `https://bazaardb.gg/run/${id}`
  }));
})()
'@
    return Invoke-BrowserEval $expression
}

function Get-RunDetail {
    $expression = @'
(() => {
  const lines = document.body.innerText
    .split('\n')
    .map(s => s.trim())
    .filter(Boolean);
  const cardLinks = Array.from(document.querySelectorAll('a[href*="/card/"]'))
    .map(a => {
      const match = a.href.match(/\/card\/([^/]+)\/([^/?#]+)/);
      if (!match) return null;
      const name = decodeURIComponent(match[2]).replaceAll('-', ' ');
      return { id: match[1], name, url: a.href };
    })
    .filter(Boolean);
  const seenCards = new Set();
  const cards = cardLinks.filter(card => {
    const key = `${card.id}:${card.name}`;
    if (seenCards.has(key)) return false;
    seenCards.add(key);
    return true;
  });
  const screenshot = Array.from(document.images)
    .map(img => img.src)
    .find(src => src.includes('usercontent.bzdb.network')) || '';
  const heroIndex = lines.findIndex(line => [
    'Pygmalien', 'Vanessa', 'Dooley', 'Mak', 'Stelle', 'Jules', 'Karnok'
  ].includes(line));
  const recordIndex = lines.findIndex(line => line === 'RECORD');
  const result = lines.find(line => /VICTORY|JOURNEY/.test(line)) || '';
  const title = document.title;
  const playerFromTitle = title.match(/ by (.+?) - Bazaar DB$/)?.[1] || '';
  const heroFromTitle = title.match(/^(.+?) \d+ Wins Run by /)?.[1] || '';
  return {
    title: document.title,
    player: playerFromTitle,
    age: '',
    hero: heroIndex >= 0 ? lines[heroIndex] : heroFromTitle,
    record: recordIndex >= 0 ? lines[recordIndex + 1] || '' : '',
    result,
    screenshot,
    stats_lines: lines.slice(0, 40),
    cards
  };
})()
'@
    return Invoke-BrowserEval $expression
}

function Build-Aggregates {
    param([array]$Runs)

    $groups = $Runs |
        Where-Object { $_["hero"] -and $_["cards"].Count -gt 0 } |
        Group-Object { $_["hero"] }

    $aggregates = @{}
    foreach ($group in $groups) {
        $cardCounts = @{}
        foreach ($run in $group.Group) {
            foreach ($card in $run["cards"]) {
                if (-not $cardCounts.ContainsKey($card.name)) {
                    $cardCounts[$card.name] = 0
                }
                $cardCounts[$card.name] += 1
            }
        }

        $topCards = $cardCounts.GetEnumerator() |
            Sort-Object @{ Expression = "Value"; Descending = $true }, @{ Expression = "Name"; Ascending = $true } |
            Select-Object -First 40 |
            ForEach-Object {
                @{
                    name = $_.Name
                    count = $_.Value
                    frequency = [Math]::Round($_.Value / $group.Count, 4)
                }
            }

        $aggregates[$group.Name] = @{
            source = "bazaardb.gg/run"
            imported_at_utc = [DateTime]::UtcNow.ToString("o")
            sample_count = $group.Count
            top_cards = @($topCards)
        }
    }

    return $aggregates
}

Connect-BazaarDbTab
try {
    Navigate-And-Wait $ListUrl
    $runList = @(Get-RunList | Select-Object -First $Limit)
    $runs = @()

    for ($i = 0; $i -lt $runList.Count; $i++) {
        $run = $runList[$i]
        Write-Host ("[{0}/{1}] {2}" -f ($i + 1), $runList.Count, $run.url)
        Navigate-And-Wait $run.url
        $detail = Get-RunDetail
        if (-not $detail.hero -or $detail.cards.Count -lt 2) {
            Write-Warning "Skipping run with incomplete detail data: $($run.url)"
            continue
        }
        $runs += @{
            id = $run.id
            url = $run.url
            imported_at_utc = [DateTime]::UtcNow.ToString("o")
            player = $detail.player
            age = $detail.age
            hero = $detail.hero
            record = $detail.record
            result = $detail.result
            screenshot = $detail.screenshot
            cards = @($detail.cards)
            stats_lines = @($detail.stats_lines)
            title = $detail.title
        }
    }

    $outputFullPath = Join-Path $ProjectRoot $OutputPath
    $buildFullPath = Join-Path $ProjectRoot $BuildOutputPath
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $outputFullPath) | Out-Null
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $buildFullPath) | Out-Null

    $runs | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $outputFullPath -Encoding UTF8
    Build-Aggregates $runs | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $buildFullPath -Encoding UTF8

    Write-Host "Wrote $outputFullPath"
    Write-Host "Wrote $buildFullPath"
}
finally {
    if ($script:WebSocket) {
        $script:WebSocket.Dispose()
    }
}
