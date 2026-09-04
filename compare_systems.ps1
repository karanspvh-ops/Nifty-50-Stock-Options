# compare_systems.ps1
# Run this on BOTH machines. Compare the output line-by-line.
# Any difference = the two systems are NOT in sync.
# Usage: .\compare_systems.ps1 | Tee-Object system_fingerprint.txt

$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ROOT

function Section($title) {
    Write-Output ""
    Write-Output "══════════════════════════════════════════"
    Write-Output "  $title"
    Write-Output "══════════════════════════════════════════"
}

function FileHash($path) {
    if (Test-Path $path) {
        $h = (Get-FileHash $path -Algorithm SHA256).Hash.Substring(0,12)
        Write-Output "$path  [$h]"
    } else {
        Write-Output "$path  [MISSING]"
    }
}

# ── 1. Git ─────────────────────────────────────────────────────────────────────
Section "GIT STATE"
Write-Output "Branch  : $(git rev-parse --abbrev-ref HEAD 2>$null)"
Write-Output "HEAD    : $(git rev-parse HEAD 2>$null)"
Write-Output "Dirty   : $(if ((git status --porcelain 2>$null) -ne '') { 'YES — uncommitted changes' } else { 'No' })"
Write-Output ""
Write-Output "Last 10 commits:"
git log --oneline -10 2>$null

# ── 2. Critical strategy files ─────────────────────────────────────────────────
Section "FILE CHECKSUMS — STRATEGY"
$stratFiles = @(
    "backend\strategies\es\params.py",
    "backend\strategies\es\strategy.py",
    "backend\strategies\es\entry.py",
    "backend\strategies\es\filters.py",
    "backend\strategies\es\manage.py",
    "backend\strategies\es\exit.py",
    "backend\strategies\es\state.py",
    "backend\strategies\es\types.py",
    "backend\strategies\ob\strategy.py",
    "backend\strategies\ob\entry.py",
    "backend\strategies\ob\params.py"
)
foreach ($f in $stratFiles) { FileHash $f }

# ── 3. Critical core files ─────────────────────────────────────────────────────
Section "FILE CHECKSUMS — CORE"
$coreFiles = @(
    "backend\core\market_state.py",
    "backend\core\tick_engine.py",
    "backend\core\candle_builder.py",
    "backend\core\session_manager.py",
    "backend\core\settings_manager.py",
    "backend\core\stock_universe.py",
    "backend\execution\order_executor.py",
    "backend\execution\option_selector.py",
    "backend\core\risk_engine.py",
    "backend\reporting\costs.py"
)
foreach ($f in $coreFiles) { FileHash $f }

# ── 4. ES params (live values) ─────────────────────────────────────────────────
Section "ES PARAMS (LIVE)"
$paramsFile = "es_params.json"
if (Test-Path $paramsFile) {
    Get-Content $paramsFile
} else {
    Write-Output "es_params.json not found — using hardcoded defaults in params.py"
}

Write-Output ""
Write-Output "LOOP_SEC + time gates from params.py:"
$paramsPath = Join-Path $ROOT "backend\strategies\es\params.py"
$paramsContent = Get-Content $paramsPath -Raw
$loopSec      = [regex]::Match($paramsContent, 'LOOP_SEC\s*=\s*(\d+)').Groups[1].Value
$candleRefreq = [regex]::Match($paramsContent, 'CANDLE_REFREQ\s*=\s*(\d+)').Groups[1].Value
$warmStart    = [regex]::Match($paramsContent, 'WARM_START\s*=\s*dtime\(([^)]+)\)').Groups[1].Value
$scanStart    = [regex]::Match($paramsContent, 'SCAN_START\s*=\s*dtime\(([^)]+)\)').Groups[1].Value
$entryStart   = [regex]::Match($paramsContent, 'ENTRY_START\s*=\s*dtime\(([^)]+)\)').Groups[1].Value
$entryEnd     = [regex]::Match($paramsContent, 'ENTRY_END\s*=\s*dtime\(([^)]+)\)').Groups[1].Value
$squareOff    = [regex]::Match($paramsContent, 'SQUARE_OFF\s*=\s*dtime\(([^)]+)\)').Groups[1].Value
Write-Output "  LOOP_SEC      = $loopSec"
Write-Output "  CANDLE_REFREQ = $candleRefreq"
Write-Output "  WARM_START    = $warmStart"
Write-Output "  SCAN_START    = $scanStart"
Write-Output "  ENTRY_START   = $entryStart"
Write-Output "  ENTRY_END     = $entryEnd"
Write-Output "  SQUARE_OFF    = $squareOff"

# ── 5. Python environment ──────────────────────────────────────────────────────
Section "PYTHON ENVIRONMENT"
Write-Output "Python  : $(python --version 2>&1)"
Write-Output ""
pip show fastapi sqlalchemy kiteconnect pandas numpy uvicorn 2>$null | Select-String "^(Name|Version):"

# ── 6. Database schema ─────────────────────────────────────────────────────────
Section "DATABASE SCHEMA"
if (Test-Path "trading.db") {
    $dbScript = @"
import sqlite3, sys
conn = sqlite3.connect('trading.db')
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
print('Tables:', tables)
for t in tables:
    cur.execute(f'PRAGMA table_info({t})')
    cols = [r[1] for r in cur.fetchall()]
    print(f'  {t}: {cols}')
conn.close()
"@
    $dbScript | python 2>$null
} else {
    Write-Output "trading.db NOT FOUND"
}

# ── 7. Settings / config files ─────────────────────────────────────────────────
Section "CONFIG FILES"
$configFiles = @(
    ".env.example",
    "requirements.txt",
    "pyproject.toml",
    "ecosystem.config.js",
    "ecosystem.config.cjs",
    ".claude\launch.json"
)
foreach ($f in $configFiles) { FileHash $f }

# ── 8. Backend reachability ────────────────────────────────────────────────────
Section "BACKEND PING"
try {
    $r = Invoke-RestMethod "http://localhost:8000/api/market/status" -TimeoutSec 3
    Write-Output "feed_connected  : $($r.feed_connected)"
    Write-Output "trading_halted  : $($r.trading_halted)"
    Write-Output "stocks_tracked  : $($r.stocks_tracked)"
    Write-Output "last_tick       : $($r.last_tick)"
} catch {
    Write-Output "Backend UNREACHABLE at http://localhost:8000"
}

# ── 9. PM2 / process status ────────────────────────────────────────────────────
Section "PROCESS STATUS"
try {
    $pm2 = pm2 jlist 2>$null | ConvertFrom-Json
    foreach ($p in $pm2) {
        Write-Output "$($p.name)  status=$($p.pm2_env.status)  pid=$($p.pid)  restarts=$($p.pm2_env.restart_time)"
    }
} catch {
    Write-Output "PM2 not found or not running"
    # Fallback: check if uvicorn is running
    $proc = Get-Process -Name python -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Output "Python processes running: $($proc.Count)"
    } else {
        Write-Output "No Python processes found"
    }
}

# ── 10. .gitignore check ───────────────────────────────────────────────────────
Section ".ENV / SECRETS CHECK"
if (Test-Path ".gitignore") {
    $gi = Get-Content ".gitignore"
    Write-Output ".env in .gitignore  : $(if ($gi -match '\.env') { 'YES (safe)' } else { 'NO — WARNING' })"
    Write-Output "*.json secrets      : $(if ($gi -match 'kite_token|access_token') { 'YES (safe)' } else { 'not explicitly listed' })"
} else {
    Write-Output ".gitignore MISSING"
}
if (Test-Path ".env") {
    Write-Output ".env file EXISTS locally (not committed — good)"
} else {
    Write-Output ".env file NOT found — may need setup on this machine"
}

Write-Output ""
Write-Output "══════════════════════════════════════════"
Write-Output "  FINGERPRINT COMPLETE"
Write-Output "  Compare this output with the other machine."
Write-Output "  Lines that differ = systems are out of sync."
Write-Output "══════════════════════════════════════════"
