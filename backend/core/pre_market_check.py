"""
pre_market_check.py — 8:45 AM daily GO / NO-GO health check.

Runs before market open and emails a pass/fail summary so bugs are
caught before 9:15, not during live trading.

Checks:
  1. DB reachable — can connect and read the trades table
  2. Kite token valid — kite_token.json exists with today's date
  3. Instruments cache fresh — universe_tokens.json updated today
  4. Clock sane — now_ist() returns IST within ±5 min of wall clock
  5. Strategy config loadable — ES params + settings accessible

Triggered automatically at 8:45 AM IST via DailyReportScheduler,
and available as a manual API endpoint.
"""

import os, json, threading, time
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import List, Tuple

IST = timezone(timedelta(hours=5, minutes=30))

ROOT = Path(__file__).resolve().parents[2]
TOKEN_PATH      = ROOT / "kite_token.json"
TOKENS_CACHE    = ROOT / "universe_tokens.json"
INSTRUMENT_CACHE = ROOT / "instrument_cache.json"


# ── Individual checks ─────────────────────────────────────────────────────────

def _check_db() -> Tuple[bool, str]:
    try:
        from backend.database import Session, Trade
        with Session() as db:
            count = db.query(Trade).count()
        return True, f"DB reachable — {count} total trades"
    except Exception as e:
        return False, f"DB error: {e}"


def _check_kite_token() -> Tuple[bool, str]:
    if not TOKEN_PATH.exists():
        return False, "kite_token.json not found — Zerodha login required"
    try:
        d = json.loads(TOKEN_PATH.read_text())
        token_date = d.get("date", "")
        today = date.today().isoformat()
        if token_date != today:
            return False, f"Token is stale (date={token_date}, today={today}) — re-login required"
        user = d.get("user_name", "unknown")
        return True, f"Token valid for today | user: {user}"
    except Exception as e:
        return False, f"Token read error: {e}"


def _check_instruments() -> Tuple[bool, str]:
    for path, label in [(TOKENS_CACHE, "universe_tokens.json"),
                        (INSTRUMENT_CACHE, "instrument_cache.json")]:
        if not path.exists():
            return False, f"{label} missing — run refresh_instrument_list()"
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        age_h = (datetime.now() - mtime).total_seconds() / 3600
        if age_h > 20:
            return False, f"{label} is {age_h:.0f}h old — may be stale (expected refresh at login)"
    return True, "Instrument caches present and recent"


def _check_clock() -> Tuple[bool, str]:
    from backend.core.clock import now_ist
    ist_now     = now_ist()
    utc_now     = datetime.utcnow()
    # IST = UTC+5:30 — check the offset is correct
    expected    = utc_now + timedelta(hours=5, minutes=30)
    drift_sec   = abs((ist_now - expected).total_seconds())
    if drift_sec > 300:
        return False, f"Clock drift too large: {drift_sec:.0f}s (IST={ist_now}, expected≈{expected})"
    return True, f"Clock OK — IST {ist_now.strftime('%H:%M:%S')} (drift {drift_sec:.0f}s)"


def _check_strategy_config() -> Tuple[bool, str]:
    issues = []
    try:
        from backend.core.settings_manager import get_settings
        s = get_settings()
        if not s:
            issues.append("settings empty")
    except Exception as e:
        issues.append(f"settings error: {e}")

    try:
        from backend.core.early_scalp import early_scalp
        p = early_scalp.get_params()
        required = ["hard_sl_pct", "target_pct", "trail_activate_pct", "max_positions"]
        missing = [k for k in required if k not in p]
        if missing:
            issues.append(f"ES params missing: {missing}")
    except Exception as e:
        issues.append(f"ES config error: {e}")

    if issues:
        return False, "Strategy config issues: " + "; ".join(issues)
    return True, "ES config loaded OK"


# ── Run all checks ────────────────────────────────────────────────────────────

CHECK_NAMES = ["DB", "Kite Token", "Instruments", "Clock", "Strategy Config"]
CHECK_FNS   = [_check_db, _check_kite_token, _check_instruments,
               _check_clock, _check_strategy_config]


def run_checks() -> dict:
    results = []
    for name, fn in zip(CHECK_NAMES, CHECK_FNS):
        try:
            ok, msg = fn()
        except Exception as e:
            ok, msg = False, f"Unexpected error: {e}"
        results.append({"check": name, "ok": ok, "msg": msg})

    passed  = sum(1 for r in results if r["ok"])
    total   = len(results)
    go      = passed == total
    return {
        "go":      go,
        "passed":  passed,
        "total":   total,
        "results": results,
        "run_at":  datetime.now(IST).isoformat(),
    }


# ── Email sender ──────────────────────────────────────────────────────────────

def send_check_email(report: dict):
    import smtplib, os
    from email.mime.multipart import MIMEMultipart
    from email.mime.text      import MIMEText

    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    if not smtp_user or not smtp_pass:
        print("[PRECHECK] Skipping email — SMTP_USER/SMTP_PASS not set")
        return

    go    = report["go"]
    emoji = "✅" if go else "❌"
    label = "GO" if go else "NO-GO"
    today = date.today().strftime("%d %b %Y")

    rows = ""
    for r in report["results"]:
        icon = "✅" if r["ok"] else "❌"
        rows += (
            f'<tr><td style="padding:6px 10px;border-bottom:1px solid #e5e7eb">'
            f'{icon} <b>{r["check"]}</b></td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;color:#555">'
            f'{r["msg"]}</td></tr>'
        )

    html = f"""<!DOCTYPE html><html><body style="font-family:'Segoe UI',Arial,sans-serif;color:#111;padding:24px">
<h2 style="margin:0 0 4px">{emoji} Pre-market check — {label}</h2>
<p style="color:#888;font-size:12px;margin:0 0 20px">{today} · {report["passed"]}/{report["total"]} checks passed</p>
<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;width:100%;max-width:600px;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden">
{rows}
</table>
{'<p style="color:#b91c1c;font-weight:600;margin-top:16px">⚠ Do not start trading until all checks pass.</p>' if not go else
 '<p style="color:#15803d;font-weight:600;margin-top:16px">All systems ready. Market opens at 9:15 AM IST.</p>'}
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"SPVH AMC Pre-market {label} — {today}"
    msg["From"]    = smtp_user
    msg["To"]      = smtp_user   # send to self
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, [smtp_user], msg.as_string())
        print(f"[PRECHECK] Email sent — {label}")
    except Exception as e:
        print(f"[PRECHECK] Email error: {e}")


# ── Scheduler integration ─────────────────────────────────────────────────────

class PreMarketScheduler:
    def __init__(self):
        self._timer: threading.Timer | None = None
        self._stopped = False
        self._last_run_date: str | None = None

    def start(self):
        self._stopped = False
        self._schedule_next()
        print("[PRECHECK] Scheduler started — daily check at 08:45 IST")

    def stop(self):
        self._stopped = True
        if self._timer:
            self._timer.cancel()

    def _schedule_next(self):
        if self._stopped:
            return
        now    = datetime.now(IST)
        target = now.replace(hour=8, minute=45, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        delay = (target - now).total_seconds()
        self._timer = threading.Timer(delay, self._fire)
        self._timer.daemon = True
        self._timer.start()
        print(f"[PRECHECK] Next check: {target.strftime('%Y-%m-%d %H:%M IST')} ({delay/3600:.1f}h)")

    def _fire(self):
        today = date.today().isoformat()
        if self._last_run_date == today:
            self._schedule_next()
            return
        if datetime.now(IST).weekday() >= 5:   # skip weekends
            self._schedule_next()
            return
        try:
            print("[PRECHECK] Running pre-market health check…")
            report = run_checks()
            self._last_run_date = today
            _print_report(report)
            send_check_email(report)
        except Exception as e:
            print(f"[PRECHECK] Error: {e}")
        finally:
            self._schedule_next()

    def run_now(self) -> dict:
        """Manual trigger — returns the report dict."""
        report = run_checks()
        _print_report(report)
        threading.Thread(target=send_check_email, args=(report,), daemon=True).start()
        return report


def _print_report(report: dict):
    label = "GO" if report["go"] else "NO-GO"
    print(f"[PRECHECK] === {label} ({report['passed']}/{report['total']} passed) ===")
    for r in report["results"]:
        icon = "OK " if r["ok"] else "FAIL"
        print(f"[PRECHECK]   [{icon}] {r['check']}: {r['msg']}")


pre_market_scheduler = PreMarketScheduler()
