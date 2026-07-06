import { useEffect, useState, useMemo, useCallback } from 'react';
import html2pdf from 'html2pdf.js';
import { useMarketStore } from '../../store/marketStore';

const API = 'http://localhost:8000';

// ── Types ──────────────────────────────────────────────────────────────────────
type Period = 'today' | '7d' | '30d' | 'custom' | 'all';

interface Trade {
  id: number;
  symbol: string;
  direction: string;
  strike?: number;
  option_type?: string;
  expiry?: string;
  qty?: number;
  lot_size?: number;
  entry?: number;
  exit?: number;
  peak?: number;
  peak_pct?: number;
  pnl?: number;
  pnl_pct?: number;
  entered_at?: string;
  exited_at?: string;
  entry_logic?: string;
  trade_statement?: string;
}

// ── Helpers ────────────────────────────────────────────────────────────────────
function getStrategy(logic?: string) {
  if (logic?.startsWith('[ES]')) return 'ES';
  if (logic?.startsWith('[OB]')) return 'OB';
  return 'TE';
}

function fmtPnL(v: number) {
  return `${v >= 0 ? '+' : '−'}₹${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
}

function fmtRs(v: number) {
  return `₹${v.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
}

function fmtDate(s: string) {
  return new Date(s + 'T00:00:00').toLocaleDateString('en-IN', { day: '2-digit', month: 'short', weekday: 'short' });
}

function fmtTime(s?: string | null) {
  if (!s) return '—';
  const m = String(s).match(/(\d{2}:\d{2}:\d{2})/);
  return m ? m[1] : '—';
}

function todayStr() {
  return new Date().toISOString().slice(0, 10);
}

// Normalise SQLite datetime strings like "2026-07-03 09:44:12.204113"
// (space separator, microseconds) into a Date JS can reliably parse.
function parseTs(s: string): Date {
  return new Date(s.replace(' ', 'T').slice(0, 23));
}

function cutoffForPeriod(period: Period, customFrom: string): Date {
  const d = new Date();
  if (period === 'today') { d.setHours(0, 0, 0, 0); return d; }
  if (period === '7d')    { d.setDate(d.getDate() - 6); d.setHours(0, 0, 0, 0); return d; }
  if (period === '30d')   { d.setDate(d.getDate() - 29); d.setHours(0, 0, 0, 0); return d; }
  if (period === 'custom' && customFrom) return new Date(customFrom + 'T00:00:00');
  return new Date(0); // 'all'
}

function capitalUsed(t: Trade): number {
  if (t.entry && t.qty && t.lot_size) return t.entry * t.qty * t.lot_size;
  if (t.entry && t.qty) return t.entry * t.qty;
  return 0;
}

// ₹20 + 18% GST per leg × 2 legs (buy + sell)
function calcBrokerage(): number { return 47.2; }

function calcStatutory(t: Trade): number {
  if (!t.entry || !t.exit || !t.qty) return 0;
  const ls       = t.lot_size || 1;
  const entryVal = t.entry * t.qty * ls;
  const exitVal  = t.exit  * t.qty * ls;
  const stt         = 0.00125  * exitVal;                         // 0.125% on sell premium
  const exchange    = 0.00053  * (entryVal + exitVal);            // NSE F&O transaction charge
  const gstOnExch   = 0.18     * exchange;                        // 18% GST on exchange charges
  const sebi        = (entryVal + exitVal) * 10 / 10_000_000;    // ₹10 per crore
  const stamp       = 0.00003  * entryVal;                        // 0.003% stamp on buy
  return Math.round(stt + exchange + gstOnExch + sebi + stamp);
}

function profitFactor(trades: Trade[]): number {
  const gross_profit = trades.filter(t => (t.pnl || 0) > 0).reduce((s, t) => s + (t.pnl || 0), 0);
  const gross_loss   = Math.abs(trades.filter(t => (t.pnl || 0) < 0).reduce((s, t) => s + (t.pnl || 0), 0));
  return gross_loss === 0 ? (gross_profit > 0 ? Infinity : 0) : +(gross_profit / gross_loss).toFixed(2);
}

// ── ES capital usage section (₹5L base, per-day concurrent analysis) ───────────
function esCapitalSection(esTrades: Trade[]): string {
  if (!esTrades.length) return '';
  const BASE = 500_000;
  const tradeCap = (t: Trade) => (t.entry || 0) * (t.qty || 0) * (t.lot_size || 1);

  const byDay = new Map<string, Trade[]>();
  esTrades.forEach(t => {
    const day = (t.entered_at || '').slice(0, 10);
    if (day) { if (!byDay.has(day)) byDay.set(day, []); byDay.get(day)!.push(t); }
  });
  const sortedDays = [...byDay.keys()].sort();
  let runningBal = BASE;

  const th = 'padding:6px 8px;text-align:left;font-size:9px;text-transform:uppercase;color:#1e293b;font-weight:700;letter-spacing:.5px;background:#ede9fe;border-bottom:2px solid #c4b5fd';
  const tds = 'padding:5px 8px;border-bottom:1px solid #e2e8f0;font-size:11px';

  interface DayR {
    date: string; opening: number; peak: number; headroom: number;
    breached: boolean; dayPnl: number; trades: (Trade & { _conc: number })[];
  }
  const days: DayR[] = [];

  for (const day of sortedDays) {
    const raw = [...byDay.get(day)!].sort((a, b) => (a.entered_at || '').localeCompare(b.entered_at || ''));
    const opening = runningBal;

    const rich = raw.map(t => {
      const tE = parseTs(t.entered_at || '');
      let conc = 0;
      raw.forEach(u => {
        if (!u.entered_at) return;
        const uE = parseTs(u.entered_at);
        const uX = u.exited_at ? parseTs(u.exited_at) : null;
        if (uE <= tE && (!uX || tE < uX)) conc += tradeCap(u);
      });
      return { ...t, _conc: Math.round(conc) };
    });

    const evs: { ts: number; d: number }[] = [];
    raw.forEach(t => {
      const c = tradeCap(t);
      if (!c) return;
      if (t.entered_at) evs.push({ ts: parseTs(t.entered_at).getTime(), d: +c });
      if (t.exited_at)  evs.push({ ts: parseTs(t.exited_at).getTime(),  d: -c });
    });
    evs.sort((a, b) => a.ts - b.ts);
    let rc = 0, peak = 0;
    evs.forEach(e => { rc += e.d; if (rc > peak) peak = rc; });

    const dayPnl = raw.reduce((s, t) => s + (t.pnl || 0), 0);
    days.push({ date: day, opening, peak: Math.round(peak), headroom: Math.round(opening - peak), breached: peak > opening, dayPnl, trades: rich });
    runningBal += dayPnl;
  }

  const summaryRows = days.map(d => {
    const pct  = d.opening ? (d.peak / d.opening * 100).toFixed(1) + '%' : '0%';
    const sc   = d.breached ? '#b91c1c' : '#15803d';
    const hc   = d.headroom < 0 ? '#b91c1c' : '#15803d';
    const pc   = d.dayPnl >= 0 ? '#15803d' : '#b91c1c';
    const lbl  = (() => { try { return new Date(d.date + 'T00:00:00').toLocaleDateString('en-IN', { day: '2-digit', month: 'short' }); } catch { return d.date; } })();
    return `<tr>
      <td style="${tds};color:#0f172a;font-weight:600">${lbl}</td>
      <td style="${tds}">₹${d.opening.toLocaleString('en-IN')}</td>
      <td style="${tds};color:#b45309;font-weight:600">₹${d.peak.toLocaleString('en-IN')}</td>
      <td style="${tds};color:${hc}">₹${Math.abs(d.headroom).toLocaleString('en-IN')}</td>
      <td style="${tds}">${pct}</td>
      <td style="${tds};color:${sc};font-weight:600">${d.breached ? '⚠ OVER LIMIT' : '✓ Within limit'}</td>
      <td style="${tds};color:${pc};font-weight:600">${d.dayPnl >= 0 ? '+' : '−'}₹${Math.abs(Math.round(d.dayPnl)).toLocaleString('en-IN')}</td>
    </tr>`;
  }).join('');

  const summaryTbl = `<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;margin-bottom:20px">
    <thead><tr>
      <th style="${th}">Date</th><th style="${th}">Opening</th>
      <th style="${th}">Peak Concurrent</th><th style="${th}">Headroom</th>
      <th style="${th}">% Used</th><th style="${th}">Status</th><th style="${th}">Day PnL</th>
    </tr></thead><tbody>${summaryRows}</tbody></table>`;

  const detailHtml = days.map(d => {
    const dlabel = (() => { try { return new Date(d.date + 'T00:00:00').toLocaleDateString('en-IN', { weekday: 'long', day: '2-digit', month: 'short', year: 'numeric' }); } catch { return d.date; } })();
    const rows = d.trades.map((t, i) => {
      const cap = tradeCap(t);
      const pnl = t.pnl || 0;
      const pc  = pnl >= 0 ? '#15803d' : '#b91c1c';
      const cc  = t._conc > d.opening ? '#b91c1c' : '#0f172a';
      const bg  = i % 2 === 0 ? '#ffffff' : '#faf5ff';
      let dur = '';
      if (t.entered_at && t.exited_at) {
        const s = Math.round((parseTs(t.exited_at).getTime() - parseTs(t.entered_at).getTime()) / 1000);
        dur = `${Math.floor(s / 60)}m ${s % 60}s`;
      }
      const sym = [t.symbol, t.strike != null ? String(t.strike) : '', t.option_type || ''].filter(Boolean).join(' ');
      return `<tr style="background:${bg}">
        <td style="${tds};font-weight:600;color:#0f172a">${sym}</td>
        <td style="${tds};color:#475569;font-family:monospace">${fmtTime(t.entered_at)}</td>
        <td style="${tds};color:#475569;font-family:monospace">${fmtTime(t.exited_at)}</td>
        <td style="${tds};color:#475569">${dur}</td>
        <td style="${tds}">₹${Math.round(cap).toLocaleString('en-IN')}</td>
        <td style="${tds};color:${cc};font-weight:600">₹${t._conc.toLocaleString('en-IN')}</td>
        <td style="${tds};color:${pc};font-weight:700">${pnl >= 0 ? '+' : '−'}₹${Math.abs(Math.round(pnl)).toLocaleString('en-IN')}</td>
      </tr>`;
    }).join('');
    return `<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;margin-bottom:16px">
      <thead>
        <tr style="background:#f5f0ff">
          <td colspan="5" style="padding:6px 8px;font-size:11px;font-weight:700;color:#7c3aed">${dlabel}</td>
          <td colspan="2" style="padding:6px 8px;text-align:right;font-size:11px;color:#555">Peak ₹${d.peak.toLocaleString('en-IN')} / Opening ₹${d.opening.toLocaleString('en-IN')} &nbsp;·&nbsp; ${d.breached ? '⚠ OVER LIMIT' : '✓ Within limit'}</td>
        </tr>
        <tr>
          <th style="${th}">Symbol</th><th style="${th}">In</th><th style="${th}">Out</th>
          <th style="${th}">Duration</th><th style="${th}">Capital</th>
          <th style="${th}">Concurrent at Entry</th><th style="${th}">PnL</th>
        </tr>
      </thead><tbody>${rows}</tbody></table>`;
  }).join('');

  return `<div style="margin-top:28px;padding-top:16px;border-top:2px solid #c4b5fd">
    <div style="font-weight:700;font-size:14px;color:#7c3aed;margin-bottom:4px">ES — Capital Usage (₹5L Base)</div>
    <div style="font-size:11px;color:#555;margin-bottom:16px">₹5,00,000 base capital &nbsp;·&nbsp; ₹1L/trade budget &nbsp;·&nbsp; balance updates daily with PnL</div>
    ${summaryTbl}${detailHtml}
  </div>`;
}

// ── PDF generator ──────────────────────────────────────────────────────────────
function generatePDF(
  period: string,
  customFrom: string,
  customTo: string,
  esTrades: Trade[],
  obTrades: Trade[],
  allTrades: Trade[],
) {
  const today = todayStr();

  const dateLabel =
    period === 'today'  ? today :
    period === '7d'     ? `Last 7 days (to ${today})` :
    period === '30d'    ? `Last 30 days (to ${today})` :
    period === 'custom' ? `${customFrom} to ${customTo}` :
    'All Time';

  const dateSuffix = (() => {
    if (period === 'today')  return today;
    if (period === '7d') {
      const d = new Date(); d.setDate(d.getDate() - 6);
      return `${d.toISOString().slice(0, 10)}_to_${today}`;
    }
    if (period === '30d') {
      const d = new Date(); d.setDate(d.getDate() - 29);
      return `${d.toISOString().slice(0, 10)}_to_${today}`;
    }
    if (period === 'custom') return `${customFrom}_to_${customTo}`;
    if (allTrades.length) {
      const dates = allTrades.map(t => (t.entered_at || '').slice(0, 10)).filter(Boolean).sort();
      return `${dates[0]}_to_${dates[dates.length - 1]}`;
    }
    return 'All_Time';
  })();

  const filename = `SPVH_AMC_Report_${dateSuffix}.pdf`;

  const calcStats = (ts: Trade[]) => {
    const net    = ts.reduce((s, t) => s + (t.pnl || 0), 0);
    const wins   = ts.filter(t => (t.pnl || 0) > 0).length;
    const caps   = ts.map(capitalUsed).filter(Boolean);
    const avgCap = caps.length ? Math.round(caps.reduce((a, b) => a + b, 0) / caps.length) : 0;
    const pf     = profitFactor(ts);
    return { n: ts.length, net, wins, winPct: ts.length ? Math.round(wins / ts.length * 100) : 0, pf, avgCap };
  };
  const all = calcStats(allTrades);
  const es  = calcStats(esTrades);
  const ob  = calcStats(obTrades);

  const statsRow = (s: ReturnType<typeof calcStats>) => {
    const pnlColor = s.net >= 0 ? '#16a34a' : '#dc2626';
    const stat = (label: string, val: string, color = '#0f172a') =>
      `<td style="padding:4px 24px 4px 0;vertical-align:top">
        <div style="font-size:9px;text-transform:uppercase;color:#475569;letter-spacing:.6px;margin-bottom:4px">${label}</div>
        <div style="font-size:20px;font-weight:800;color:${color};line-height:1">${val}</div>
      </td>`;
    return `<table cellpadding="0" cellspacing="0" style="width:100%;margin:6px 0"><tr>
      ${stat('Total Trades', String(s.n))}
      ${stat('Win Rate', `${s.winPct}%`)}
      ${stat('Net PnL', fmtPnL(s.net), pnlColor)}
      ${stat('Profit Factor', isFinite(s.pf) ? String(s.pf) : '∞')}
      ${stat('Avg Capital', s.avgCap ? fmtRs(s.avgCap) : '—')}
    </tr></table>`;
  };

  const tradeTable = (trades: Trade[]) => {
    if (!trades.length) return '<p style="color:#555;padding:8px;font-size:12px">No trades in this period.</p>';
    const thStyle = 'padding:6px 8px;text-align:left;font-size:9px;text-transform:uppercase;color:#1e293b;font-weight:700;letter-spacing:.5px;background:#dde4ee;border-bottom:2px solid #b0bec5';
    const td = 'padding:5px 8px;border-bottom:1px solid #e2e8f0;color:#111;font-size:11px';

    const byDate = new Map<string, Trade[]>();
    trades.forEach(t => {
      const day = (t.entered_at || '').slice(0, 10) || 'Unknown';
      if (!byDate.has(day)) byDate.set(day, []);
      byDate.get(day)!.push(t);
    });
    const sortedDates = Array.from(byDate.keys()).sort();

    const fmtDay = (d: string) => {
      try {
        return new Date(d + 'T00:00:00').toLocaleDateString('en-IN', { weekday: 'long', day: '2-digit', month: 'long', year: 'numeric' });
      } catch { return d; }
    };

    return sortedDates.map(day => {
      const dayTrades = byDate.get(day)!;
      const dayPnl   = dayTrades.reduce((s, t) => s + (t.pnl || 0), 0);
      const dayBg    = dayPnl >= 0 ? '#dcfce7' : '#fee2e2';
      const dayColor = dayPnl >= 0 ? '#15803d' : '#b91c1c';
      const daySign  = dayPnl >= 0 ? '+' : '−';

      let rows = '';
      dayTrades.forEach((t, i) => {
        const cap        = capitalUsed(t);
        const broker     = calcBrokerage();
        const statutory  = calcStatutory(t);
        const pnlColor   = (t.pnl || 0) >= 0 ? '#15803d' : '#b91c1c';
        const peakColor  = (t.peak_pct ?? 0) >= 0 ? '#15803d' : '#b91c1c';
        const bg         = i % 2 === 0 ? '#ffffff' : '#f8fafc';
        const symLabel   = [
          t.symbol,
          t.strike != null ? String(t.strike) : '',
          t.option_type || '',
          t.qty != null  ? `${t.qty} QTY` : '',
        ].filter(Boolean).join(' ');
        const peakCell = t.peak != null
          ? `₹${t.peak.toFixed(2)} <span style="font-size:10px">(${(t.peak_pct ?? 0) >= 0 ? '+' : ''}${(t.peak_pct ?? 0).toFixed(1)}%)</span>`
          : '—';
        rows += `<tr style="background:${bg};page-break-inside:avoid">
          <td style="${td};font-weight:600;color:#0f172a">${symLabel}</td>
          <td style="${td}">${t.entry ? `₹${t.entry.toFixed(2)}` : '—'}</td>
          <td style="${td}">${t.exit  ? `₹${t.exit.toFixed(2)}`  : '—'}</td>
          <td style="${td}">${cap ? fmtRs(cap) : '—'}</td>
          <td style="${td};color:${pnlColor};font-weight:700">${fmtPnL(t.pnl || 0)}</td>
          <td style="${td};color:${pnlColor};font-weight:600">${(t.pnl_pct || 0).toFixed(1)}%</td>
          <td style="${td};color:${peakColor}">${peakCell}</td>
          <td style="${td};color:#64748b">₹${broker.toFixed(0)}</td>
          <td style="${td};color:#64748b">₹${statutory}</td>
          <td style="${td};color:#475569">${fmtTime(t.entered_at)}</td>
          <td style="${td};color:#475569">${fmtTime(t.exited_at)}</td>
        </tr>`;
      });

      // Single unified table per day — thead contains date-band + column headers so they
      // travel together and never split across pages.
      return `<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;margin-bottom:20px">
        <thead>
          <tr style="background:${dayBg};page-break-after:avoid">
            <td colspan="9" style="padding:7px 10px;font-size:12px;font-weight:700;color:#1e293b">${fmtDay(day)}</td>
            <td colspan="2" style="padding:7px 10px;text-align:right;font-size:12px;font-weight:700;color:${dayColor}">${daySign}₹${Math.abs(Math.round(dayPnl)).toLocaleString('en-IN')} &nbsp;·&nbsp; ${dayTrades.length} trade${dayTrades.length !== 1 ? 's' : ''}</td>
          </tr>
          <tr style="page-break-after:avoid">
            <th style="${thStyle}">Symbol</th>
            <th style="${thStyle}">Entry</th><th style="${thStyle}">Exit</th>
            <th style="${thStyle}">Capital</th><th style="${thStyle}">PnL</th><th style="${thStyle}">%</th>
            <th style="${thStyle}">Peak</th>
            <th style="${thStyle}">Brokerage</th><th style="${thStyle}">Statutory</th>
            <th style="${thStyle}">In</th><th style="${thStyle}">Out</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
    }).join('');
  };

  const html = `<!DOCTYPE html><html><head>
  <meta charset="utf-8"/>
  <title>SPVH AMC — Trading Report</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Segoe UI',Arial,sans-serif;color:#111;background:#fff;padding:20px 24px;font-size:12px}
    table{border-collapse:collapse}
    thead tr:last-child th{page-break-after:avoid}
    tr{page-break-inside:avoid}
  </style>
  </head><body>
  <h1 style="font-size:22px;font-weight:800;color:#0f172a;margin-bottom:4px">SPVH AMC — Trading Report</h1>
  <p style="color:#555;font-size:11px;margin-bottom:20px">
    Period: ${dateLabel} &bull; Generated: ${new Date().toLocaleString('en-IN')} &bull; Mode: PAPER
  </p>

  <div style="background:#f1f5f9;border:1px solid #cbd5e1;border-radius:6px;padding:16px;margin-bottom:24px">
    <div style="font-weight:700;font-size:13px;color:#0f172a;margin-bottom:10px;text-transform:uppercase;letter-spacing:.5px">Combined Summary</div>
    ${statsRow(all)}
  </div>

  <div style="page-break-before:always;break-before:page;margin-bottom:32px">
    <div style="border-top:3px solid #a855f7;background:#f5f0ff;padding:14px 16px;margin-bottom:12px">
      <div style="color:#7c3aed;font-weight:700;font-size:16px;margin-bottom:8px">Early Scalp [ES]</div>
      ${statsRow(es)}
    </div>
    ${tradeTable(esTrades)}
    ${esCapitalSection(esTrades)}
  </div>

  <div style="page-break-before:always;break-before:page;margin-bottom:32px">
    <div style="border-top:3px solid #3b82f6;background:#eff6ff;padding:14px 16px;margin-bottom:12px">
      <div style="color:#1d4ed8;font-weight:700;font-size:16px;margin-bottom:8px">Opening Breakout [OB]</div>
      ${statsRow(ob)}
    </div>
    ${tradeTable(obTrades)}
  </div>
  </body></html>`;

  // Pass HTML string directly — html2pdf creates its own hidden iframe,
  // avoiding the blank-PDF bug caused by off-screen fixed-position elements.
  html2pdf()
    .set({
      margin:      [10, 10, 10, 10],
      filename,
      image:       { type: 'jpeg', quality: 0.95 },
      html2canvas: { scale: 2, useCORS: true, logging: false },
      jsPDF:       { unit: 'mm', format: 'a4', orientation: 'landscape' },
      pagebreak:   { mode: ['css', 'legacy'], before: '[data-page-break]' },
    } as any)
    .from(html)
    .save();
}

// ── Email modal ────────────────────────────────────────────────────────────────
function EmailModal({
  onClose, onSend
}: {
  onClose: () => void;
  onSend: (email: string) => Promise<void>;
}) {
  const [email, setEmail] = useState('spventures.inv@gmail.com');
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  const send = async () => {
    setSending(true);
    try {
      await onSend(email);
      setResult('success');
    } catch (e: any) {
      setResult('error: ' + (e.message || 'Failed'));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-surface border border-border rounded-xl p-6 w-full max-w-sm space-y-4 shadow-2xl">
        <div className="flex items-center justify-between">
          <h3 className="text-white font-semibold">Email Report</h3>
          <button onClick={onClose} className="text-muted hover:text-white text-lg leading-none">✕</button>
        </div>
        {result === 'success' ? (
          <div className="text-up text-sm">Report sent successfully!</div>
        ) : (
          <>
            <div>
              <label className="text-muted text-xs uppercase tracking-wider block mb-1">Recipient Email</label>
              <input
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-accent"
              />
            </div>
            {result && result.startsWith('error') && (
              <div className="text-down text-xs">{result}</div>
            )}
            <div className="flex gap-2 justify-end">
              <button onClick={onClose} className="px-4 py-2 text-sm text-muted hover:text-white border border-border rounded-lg">Cancel</button>
              <button onClick={send} disabled={sending || !email}
                className="px-4 py-2 text-sm bg-accent text-white rounded-lg hover:bg-blue-600 disabled:opacity-50">
                {sending ? 'Sending…' : 'Send Report'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────
export default function ReportsView() {
  const { settings } = useMarketStore();
  const env = settings.is_live ? 'live' : 'paper';

  // Period selection
  const [period, setPeriod]       = useState<Period>('today');
  const [customFrom, setCustomFrom] = useState('');
  const [customTo,   setCustomTo]   = useState(todayStr());

  // All trades (raw, from /api/trades)
  const [allTrades, setAllTrades] = useState<Trade[]>([]);

  // Generated pnl report (for trade_statement enrichment — today only)
  const [pnl,  setPnl]  = useState<any>(null);
  const [ml,   setMl]   = useState<any>(null);
  const [trd,  setTrd]  = useState<any>(null);
  const [tab,  setTab]  = useState<'pnl' | 'ml' | 'tradable'>('pnl');
  const [loading, setLoading] = useState(false);
  const [showEmail, setShowEmail] = useState(false);

  // Load all trades for live stats — map API field names to our interface
  const loadTrades = useCallback(() => {
    fetch(`${API}/api/trades?env=${env}&all=true`)
      .then(r => r.json())
      .then((raw: any[]) => setAllTrades(raw.map(t => ({
        id:              t.id,
        symbol:          t.symbol,
        direction:       t.direction,
        strike:          t.strike,
        option_type:     t.option_type,
        expiry:          t.expiry,
        qty:             t.quantity,
        lot_size:        t.lot_size,
        entry:           t.entry_price,
        exit:            t.exit_price,
        peak:            t.highest_price,
        peak_pct:        t.entry_price && t.highest_price
                           ? +((t.highest_price - t.entry_price) / t.entry_price * 100).toFixed(1)
                           : undefined,
        pnl:             t.pnl,
        pnl_pct:         t.pnl_pct,
        entered_at:      t.entered_at,
        exited_at:       t.exited_at,
        entry_logic:     t.entry_logic,
        trade_statement: t.trade_statement,
      }))))
      .catch(() => {});
  }, [env]);

  useEffect(() => {
    loadTrades();
    const id = setInterval(loadTrades, 15000);
    return () => clearInterval(id);
  }, [loadTrades]);

  // Load generated reports (today's pnl report + ml + tradable)
  const loadReports = useCallback(async () => {
    const [r1, r2, r3] = await Promise.all([
      fetch(`${API}/api/reports/pnl/${env}`).then(r => r.json()).catch(() => null),
      fetch(`${API}/api/reports/ml/${env}`).then(r => r.json()).catch(() => null),
      fetch(`${API}/api/reports/tradable/${env}`).then(r => r.json()).catch(() => null),
    ]);
    setPnl(r1); setMl(r2); setTrd(r3);
  }, [env]);

  useEffect(() => { loadReports(); }, [loadReports]);

  const generate = async () => {
    setLoading(true);
    await fetch(`${API}/api/reports/pnl/${env}/generate`,      { method: 'POST' });
    await fetch(`${API}/api/reports/ml/${env}/trigger`,        { method: 'POST' });
    await fetch(`${API}/api/reports/tradable/${env}/generate`, { method: 'POST' });
    loadTrades();
    await loadReports();
    setLoading(false);
  };

  // ── Filter trades by period ──────────────────────────────────────────────────
  const trades = useMemo<Trade[]>(() => {
    const cutoff = cutoffForPeriod(period, customFrom);
    let filtered = allTrades.filter(t => t.entered_at && parseTs(t.entered_at) >= cutoff);
    if (period === 'custom' && customTo) {
      const end = new Date(customTo + 'T23:59:59');
      filtered = filtered.filter(t => parseTs(t.entered_at!) <= end);
    }
    return filtered;
  }, [allTrades, period, customFrom, customTo]);

  // Enrich with trade_statement from generated pnl report
  const enrichedTrades = useMemo<Trade[]>(() => {
    if (!pnl?.trades) return trades;
    const stmtMap: Record<number, string> = {};
    (pnl.trades as any[]).forEach(t => { if (t.id && t.trade_statement) stmtMap[t.id] = t.trade_statement; });
    return trades.map(t => stmtMap[t.id] ? { ...t, trade_statement: stmtMap[t.id] } : t);
  }, [trades, pnl]);

  // ── Strategy slices ──────────────────────────────────────────────────────────
  const esTrades = useMemo(() => enrichedTrades.filter(t => getStrategy(t.entry_logic) === 'ES'), [enrichedTrades]);
  const obTrades = useMemo(() => enrichedTrades.filter(t => getStrategy(t.entry_logic) === 'OB'), [enrichedTrades]);

  const stats = useMemo(() => {
    const calc = (ts: Trade[]) => {
      const net   = ts.reduce((s, t) => s + (t.pnl || 0), 0);
      const wins  = ts.filter(t => (t.pnl || 0) > 0).length;
      const caps  = ts.map(capitalUsed).filter(Boolean);
      const avgCap = caps.length ? caps.reduce((a, b) => a + b, 0) / caps.length : 0;
      return { net, wins, n: ts.length, pf: profitFactor(ts), avgCap };
    };
    return { es: calc(esTrades), ob: calc(obTrades), all: calc(enrichedTrades) };
  }, [esTrades, obTrades, enrichedTrades]);

  // ── Per-day breakdown ────────────────────────────────────────────────────────
  const byDay = useMemo(() => {
    const map: Record<string, { es: number; ob: number; net: number; esN: number; obN: number; wins: number; n: number }> = {};
    enrichedTrades.forEach(t => {
      const d = t.entered_at?.slice(0, 10) ?? '';
      if (!map[d]) map[d] = { es: 0, ob: 0, net: 0, esN: 0, obN: 0, wins: 0, n: 0 };
      const pnl = t.pnl || 0;
      const s = getStrategy(t.entry_logic);
      if (s === 'ES') { map[d].es += pnl; map[d].esN++; }
      else if (s === 'OB') { map[d].ob += pnl; map[d].obN++; }
      map[d].net += pnl; map[d].n++;
      if (pnl > 0) map[d].wins++;
    });
    return Object.entries(map).sort((a, b) => b[0].localeCompare(a[0]));
  }, [enrichedTrades]);

  // ── Email sender ─────────────────────────────────────────────────────────────
  const sendEmail = async (email: string) => {
    const res = await fetch(`${API}/api/reports/email`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        env,
        email,
        period,
        custom_from: customFrom || null,
        custom_to:   customTo   || null,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Server error');
    }
  };

  const handleDownloadPDF = () => {
    generatePDF(period, customFrom, customTo, esTrades, obTrades, enrichedTrades);
  };

  // ── Period label ─────────────────────────────────────────────────────────────
  const periodLabel =
    period === 'today'  ? 'Today' :
    period === '7d'     ? 'Last 7 Days' :
    period === '30d'    ? 'Last 30 Days' :
    period === 'custom' ? (customFrom && customTo ? `${customFrom} → ${customTo}` : 'Custom Range') :
    'All Time';

  const PERIODS: { key: Period; label: string }[] = [
    { key: 'today', label: 'Today'       },
    { key: '7d',    label: 'Last 7 Days' },
    { key: '30d',   label: 'Last 30 Days'},
    { key: 'custom',label: 'Custom'      },
    { key: 'all',   label: 'All Time'    },
  ];

  return (
    <div className="p-6 space-y-4 overflow-y-auto h-full">
      {showEmail && (
        <EmailModal onClose={() => setShowEmail(false)} onSend={sendEmail} />
      )}

      {/* Header row */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-white font-bold text-lg">Reports</h1>
        <div className="flex items-center gap-2 flex-wrap">
          <button onClick={handleDownloadPDF}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-border rounded-lg text-muted hover:text-white hover:border-accent/50 transition-colors">
            ↓ Download PDF
          </button>
          <button onClick={() => setShowEmail(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-border rounded-lg text-muted hover:text-white hover:border-accent/50 transition-colors">
            ✉ Email Report
          </button>
          <button onClick={generate} disabled={loading}
            className="px-3 py-1.5 text-xs bg-surface border border-border text-muted hover:text-white rounded-lg disabled:opacity-50 transition-colors">
            {loading ? 'Generating…' : '↺ Refresh'}
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2">
        {([['pnl','P&L Report'], ['tradable','Tradable Windows'], ['ml','ML Retrospective']] as const).map(([t, label]) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors
              ${tab === t ? 'bg-accent text-white' : 'bg-surface border border-border text-muted hover:text-white'}`}>
            {label}
          </button>
        ))}
      </div>

      {/* ── P&L Tab ─────────────────────────────────────────────────────────── */}
      {tab === 'pnl' && (
        <div className="space-y-4">
          {/* Period selector */}
          <div className="bg-surface rounded-xl border border-border p-4 space-y-3">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[10px] uppercase tracking-wider text-muted mr-1">Period</span>
              {PERIODS.map(p => (
                <button key={p.key} onClick={() => setPeriod(p.key)}
                  className={`text-[11px] px-3 py-1 rounded font-semibold transition-colors border ${
                    period === p.key
                      ? 'bg-accent/20 text-accent border-accent/40'
                      : 'bg-border/20 text-muted border-border hover:bg-border/40'
                  }`}>
                  {p.label}
                </button>
              ))}
            </div>
            {period === 'custom' && (
              <div className="flex items-center gap-3 flex-wrap">
                <div className="flex items-center gap-2">
                  <span className="text-muted text-xs">From</span>
                  <input type="date" value={customFrom} onChange={e => setCustomFrom(e.target.value)}
                    max={customTo || todayStr()}
                    className="bg-bg border border-border rounded px-2 py-1 text-white text-xs focus:outline-none focus:border-accent" />
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-muted text-xs">To</span>
                  <input type="date" value={customTo} onChange={e => setCustomTo(e.target.value)}
                    min={customFrom} max={todayStr()}
                    className="bg-bg border border-border rounded px-2 py-1 text-white text-xs focus:outline-none focus:border-accent" />
                </div>
              </div>
            )}
          </div>

          {/* Combined summary */}
          <div className="bg-surface rounded-xl border border-border p-4">
            <div className="text-[10px] uppercase tracking-wider text-muted mb-3">{periodLabel} — Combined</div>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
              {[
                { label: 'Total Trades',   val: String(stats.all.n) },
                { label: 'Win Rate',        val: `${stats.all.n ? Math.round(stats.all.wins / stats.all.n * 100) : 0}%` },
                { label: 'Net PnL',         val: fmtPnL(stats.all.net), color: stats.all.net >= 0 ? 'text-up' : 'text-down' },
                { label: 'Profit Factor',   val: isFinite(stats.all.pf) ? String(stats.all.pf) : '∞' },
                { label: 'Avg Capital',     val: stats.all.avgCap ? fmtRs(stats.all.avgCap) : '—' },
              ].map(s => (
                <div key={s.label} className="text-center">
                  <div className="text-muted text-[10px] uppercase tracking-wider mb-1">{s.label}</div>
                  <div className={`text-white font-bold text-lg ${s.color || ''}`}>{s.val}</div>
                </div>
              ))}
            </div>
          </div>

          {/* ES + OB strategy cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* ES */}
            <div className="bg-surface rounded-xl border border-purple-500/30 bg-purple-500/5 p-4">
              <div className="text-[10px] uppercase tracking-wider text-purple-400 mb-3">Early Scalp [ES]</div>
              <div className="grid grid-cols-3 gap-3 mb-3">
                <div className="text-center">
                  <div className="text-muted text-[10px] uppercase mb-0.5">Trades</div>
                  <div className="text-white font-bold text-lg">{stats.es.n}</div>
                </div>
                <div className="text-center">
                  <div className="text-muted text-[10px] uppercase mb-0.5">Win Rate</div>
                  <div className="text-white font-bold text-lg">{stats.es.n ? Math.round(stats.es.wins / stats.es.n * 100) : 0}%</div>
                </div>
                <div className="text-center">
                  <div className="text-muted text-[10px] uppercase mb-0.5">Net PnL</div>
                  <div className={`font-bold text-lg ${stats.es.net >= 0 ? 'text-up' : 'text-down'}`}>{fmtPnL(stats.es.net)}</div>
                </div>
              </div>
              <div className="flex justify-between text-xs text-muted border-t border-border/40 pt-2">
                <span>Profit Factor: <span className="text-white">{isFinite(stats.es.pf) ? stats.es.pf : '∞'}</span></span>
                <span>Avg Capital: <span className="text-white">{stats.es.avgCap ? fmtRs(stats.es.avgCap) : '—'}</span></span>
              </div>
            </div>

            {/* OB */}
            <div className="bg-surface rounded-xl border border-blue-500/30 bg-blue-500/5 p-4">
              <div className="text-[10px] uppercase tracking-wider text-blue-400 mb-3">Opening Breakout [OB]</div>
              <div className="grid grid-cols-3 gap-3 mb-3">
                <div className="text-center">
                  <div className="text-muted text-[10px] uppercase mb-0.5">Trades</div>
                  <div className="text-white font-bold text-lg">{stats.ob.n}</div>
                </div>
                <div className="text-center">
                  <div className="text-muted text-[10px] uppercase mb-0.5">Win Rate</div>
                  <div className="text-white font-bold text-lg">{stats.ob.n ? Math.round(stats.ob.wins / stats.ob.n * 100) : 0}%</div>
                </div>
                <div className="text-center">
                  <div className="text-muted text-[10px] uppercase mb-0.5">Net PnL</div>
                  <div className={`font-bold text-lg ${stats.ob.net >= 0 ? 'text-up' : 'text-down'}`}>{fmtPnL(stats.ob.net)}</div>
                </div>
              </div>
              <div className="flex justify-between text-xs text-muted border-t border-border/40 pt-2">
                <span>Profit Factor: <span className="text-white">{isFinite(stats.ob.pf) ? stats.ob.pf : '∞'}</span></span>
                <span>Avg Capital: <span className="text-white">{stats.ob.avgCap ? fmtRs(stats.ob.avgCap) : '—'}</span></span>
              </div>
            </div>
          </div>

          {/* Per-day table — only when range spans multiple days */}
          {period !== 'today' && byDay.length > 0 && (
            <div className="bg-surface rounded-xl border border-border overflow-hidden">
              <div className="px-4 py-3 border-b border-border">
                <span className="text-white font-semibold text-sm">Daily Breakdown</span>
              </div>
              <table className="w-full text-left">
                <thead>
                  <tr className="text-muted text-[10px] uppercase tracking-wider bg-border/20 border-b border-border">
                    <th className="px-3 py-2">Date</th>
                    <th className="px-3 py-2 text-purple-400">ES</th>
                    <th className="px-3 py-2 text-blue-400">OB</th>
                    <th className="px-3 py-2">Net</th>
                    <th className="px-3 py-2">Trades</th>
                    <th className="px-3 py-2">Win%</th>
                  </tr>
                </thead>
                <tbody>
                  {byDay.map(([date, d]) => (
                    <tr key={date} className="border-b border-border/40 hover:bg-border/10 text-xs">
                      <td className="px-3 py-2 text-white font-medium">{fmtDate(date)}</td>
                      <td className={`px-3 py-2 font-mono font-semibold ${d.es >= 0 ? 'text-up' : 'text-down'}`}>
                        {d.esN > 0 ? <>{fmtPnL(d.es)}<span className="text-muted ml-1 font-normal text-[10px]">{d.esN}t</span></> : <span className="text-muted">—</span>}
                      </td>
                      <td className={`px-3 py-2 font-mono font-semibold ${d.ob >= 0 ? 'text-up' : 'text-down'}`}>
                        {d.obN > 0 ? <>{fmtPnL(d.ob)}<span className="text-muted ml-1 font-normal text-[10px]">{d.obN}t</span></> : <span className="text-muted">—</span>}
                      </td>
                      <td className={`px-3 py-2 font-mono font-bold ${d.net >= 0 ? 'text-up' : 'text-down'}`}>{fmtPnL(d.net)}</td>
                      <td className="px-3 py-2 text-muted">{d.n}</td>
                      <td className="px-3 py-2 text-muted">{d.n ? Math.round(d.wins / d.n * 100) : 0}%</td>
                    </tr>
                  ))}
                  <tr className="bg-border/20 text-xs font-bold border-t border-border">
                    <td className="px-3 py-2 text-muted text-[10px] uppercase tracking-wider">Total</td>
                    <td className={`px-3 py-2 font-mono ${stats.es.net >= 0 ? 'text-up' : 'text-down'}`}>{fmtPnL(stats.es.net)}</td>
                    <td className={`px-3 py-2 font-mono ${stats.ob.net >= 0 ? 'text-up' : 'text-down'}`}>{fmtPnL(stats.ob.net)}</td>
                    <td className={`px-3 py-2 font-mono ${stats.all.net >= 0 ? 'text-up' : 'text-down'}`}>{fmtPnL(stats.all.net)}</td>
                    <td className="px-3 py-2 text-muted">{stats.all.n}</td>
                    <td className="px-3 py-2 text-muted">{stats.all.n ? Math.round(stats.all.wins / stats.all.n * 100) : 0}%</td>
                  </tr>
                </tbody>
              </table>
            </div>
          )}

          {/* Trade log */}
          {enrichedTrades.length > 0 && (
            <div className="bg-surface rounded-xl border border-border p-4 space-y-3">
              <h3 className="text-white font-semibold text-sm">
                Trade Log
                <span className="text-muted font-normal ml-2 text-xs">
                  {enrichedTrades.length} trades · avg capital {stats.all.avgCap ? fmtRs(stats.all.avgCap) : '—'} per trade
                </span>
              </h3>
              {enrichedTrades.map(t => {
                const cap = capitalUsed(t);
                const peakCls = (t.peak_pct ?? 0) >= 0 ? 'text-up' : 'text-down';
                const strategy = getStrategy(t.entry_logic);
                return (
                  <div key={t.id} className="border border-border rounded-lg p-3 text-xs space-y-2">
                    <div className="flex items-center justify-between flex-wrap gap-2">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className={`text-[9px] px-1.5 py-0.5 rounded font-bold
                          ${strategy === 'ES' ? 'bg-purple-500/20 text-purple-400' : 'bg-blue-500/20 text-blue-400'}`}>
                          {strategy}
                        </span>
                        <span className="text-white font-semibold">{t.symbol}</span>
                        <span className={`text-[10px] px-1.5 py-0.5 rounded font-semibold
                          ${t.direction === 'call' ? 'bg-up/20 text-up' : 'bg-down/20 text-down'}`}>
                          {t.direction?.toUpperCase()}
                        </span>
                        {t.strike != null && (
                          <span className="text-[11px] font-mono text-white bg-bg/60 px-1.5 py-0.5 rounded">
                            {t.strike} {t.option_type || ''}
                            {t.expiry && <span className="text-muted ml-1">{String(t.expiry).slice(5)}</span>}
                          </span>
                        )}
                        {t.qty != null && (
                          <span className="text-[10px] text-muted font-mono">
                            {t.lot_size ? `${t.qty}×${t.lot_size}=${t.qty * t.lot_size}` : `qty ${t.qty}`}
                          </span>
                        )}
                      </div>
                      <span className={`font-semibold ${(t.pnl || 0) >= 0 ? 'text-up' : 'text-down'}`}>
                        {(t.pnl || 0) >= 0 ? '+' : ''}₹{(t.pnl || 0).toFixed(2)}
                        <span className="text-muted ml-1 text-[10px]">({(t.pnl_pct || 0).toFixed(1)}%)</span>
                      </span>
                    </div>
                    <div className="grid grid-cols-2 md:grid-cols-6 gap-x-3 gap-y-1 text-[11px]">
                      <div><span className="text-muted">Entry @ </span><span className="text-white">₹{t.entry?.toFixed(2)}</span></div>
                      <div><span className="text-muted">Exit @ </span><span className="text-white">{t.exit != null ? `₹${t.exit.toFixed(2)}` : '—'}</span></div>
                      <div><span className="text-muted">Peak </span>
                        <span className={peakCls}>{t.peak != null ? `₹${t.peak.toFixed(2)}` : '—'}</span>
                        {t.peak_pct != null && <span className={`ml-1 ${peakCls}`}>({t.peak_pct >= 0 ? '+' : ''}{t.peak_pct.toFixed(1)}%)</span>}
                      </div>
                      <div>
                        <span className="text-muted">Capital </span>
                        <span className="text-white font-semibold">{cap ? fmtRs(cap) : '—'}</span>
                      </div>
                      <div><span className="text-muted">In </span><span className="text-white font-mono">{fmtTime(t.entered_at)}</span></div>
                      <div><span className="text-muted">Out </span><span className="text-white font-mono">{fmtTime(t.exited_at)}</span></div>
                    </div>
                    {t.trade_statement && (
                      <p className="text-muted leading-relaxed">{t.trade_statement}</p>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {enrichedTrades.length === 0 && (
            <div className="bg-surface rounded-xl border border-border p-8 text-center">
              <p className="text-muted text-sm">No trades found for {periodLabel}.</p>
            </div>
          )}
        </div>
      )}

      {/* ── ML Tab ──────────────────────────────────────────────────────────── */}
      {tab === 'ml' && ml && !ml.status && (
        <div className="space-y-4">
          <div className="bg-surface rounded-xl border border-border p-4">
            <h3 className="text-white font-semibold text-sm mb-2">Analysis Summary</h3>
            <p className="text-muted text-xs leading-relaxed">{ml.summary}</p>
          </div>
          <div className="bg-surface rounded-xl border border-border p-4">
            <h3 className="text-white font-semibold text-sm mb-3">Loss Patterns</h3>
            <div className="space-y-2">
              {Object.entries(ml.patterns || {}).map(([k, v]: [string, any]) => (
                <div key={k} className="flex items-center gap-3">
                  <div className="w-32 text-[10px] text-muted truncate uppercase">{k.replace(/_/g,' ')}</div>
                  <div className="flex-1 bg-border rounded-full h-2">
                    <div className="bg-down h-2 rounded-full" style={{ width: `${v.pct_of_total}%` }} />
                  </div>
                  <div className="text-xs text-down w-12 text-right">{v.pct_of_total}%</div>
                  <div className="text-xs text-muted w-20 text-right">avg ₹{v.avg_loss}</div>
                </div>
              ))}
            </div>
          </div>
          <div className="bg-surface rounded-xl border border-border p-4 space-y-3">
            <h3 className="text-white font-semibold text-sm">Recommendations</h3>
            {(ml.recommendations || []).map((r: any, i: number) => (
              <div key={i} className="border border-accent/30 rounded-lg p-3 text-xs space-y-1 bg-accent/5">
                <div className="text-accent font-semibold">{r.pattern?.replace(/_/g,' ')}</div>
                <p className="text-muted">{r.finding}</p>
                <p className="text-white">{r.action}</p>
                {r.current !== undefined && (
                  <div className="flex gap-4 mt-1">
                    <span className="text-muted">Current: <span className="text-white">{String(r.current)}</span></span>
                    <span className="text-muted">Suggested: <span className="text-up">{String(r.suggested)}</span></span>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
      {tab === 'ml' && (!ml || ml.status) && (
        <div className="bg-surface rounded-xl border border-border p-8 text-center">
          <p className="text-muted text-sm">No ML analysis available. Click Refresh to generate.</p>
        </div>
      )}

      {/* ── Tradable Windows Tab ─────────────────────────────────────────────── */}
      {tab === 'tradable' && trd && !trd.status && (
        <div className="space-y-4">
          <div className="bg-surface rounded-xl border border-border p-4 grid grid-cols-4 gap-4">
            {[
              { label: 'Total Windows',  val: trd.summary?.total_windows },
              { label: 'Traded',         val: trd.summary?.windows_traded,  color: 'text-up' },
              { label: 'Missed',         val: trd.summary?.windows_missed,  color: 'text-muted' },
              { label: 'Avg Window',     val: `${trd.summary?.avg_window_seconds ?? 0}s` },
            ].map(s => (
              <div key={s.label} className="text-center">
                <div className="text-muted text-xs uppercase mb-1">{s.label}</div>
                <div className={`text-white font-bold text-lg ${s.color || ''}`}>{s.val}</div>
              </div>
            ))}
          </div>
          <div className="bg-surface rounded-xl border border-border p-4">
            <h3 className="text-white font-semibold text-sm mb-3">Tradable Windows Log</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead>
                  <tr className="text-muted text-[10px] uppercase tracking-wider border-b border-border">
                    <th className="px-2 pb-2">Symbol</th><th className="px-2 pb-2">Dir</th>
                    <th className="px-2 pb-2">Sector</th><th className="px-2 pb-2">Identified</th>
                    <th className="px-2 pb-2">Until</th><th className="px-2 pb-2">Duration</th>
                    <th className="px-2 pb-2">Score</th><th className="px-2 pb-2">Traded?</th>
                  </tr>
                </thead>
                <tbody>
                  {(trd.signals || []).map((s: any) => (
                    <tr key={s.id} className="border-b border-border/40 hover:bg-border/20">
                      <td className="px-2 py-1.5 text-white text-xs font-medium">{s.symbol}</td>
                      <td className="px-2 py-1.5">
                        <span className={`text-[10px] px-1.5 py-0.5 rounded font-semibold
                          ${s.direction === 'call' ? 'bg-up/20 text-up' : 'bg-down/20 text-down'}`}>
                          {s.direction?.toUpperCase()}
                        </span>
                      </td>
                      <td className="px-2 py-1.5 text-muted text-[11px]">{s.sector}</td>
                      <td className="px-2 py-1.5 text-muted text-[11px]">{new Date(s.opened_at).toLocaleTimeString('en-IN', { hour12: false })}</td>
                      <td className="px-2 py-1.5 text-muted text-[11px]">
                        {s.closed_at ? new Date(s.closed_at).toLocaleTimeString('en-IN', { hour12: false }) : <span className="text-up">active</span>}
                      </td>
                      <td className="px-2 py-1.5 text-white text-xs">{s.duration_sec != null ? `${s.duration_sec}s` : '—'}</td>
                      <td className="px-2 py-1.5 text-accent text-xs">{s.entry_score}/{s.max_score}</td>
                      <td className="px-2 py-1.5 text-xs">{s.was_traded ? <span className="text-up">✓</span> : <span className="text-muted">—</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
      {tab === 'tradable' && (!trd || trd.status) && (
        <div className="bg-surface rounded-xl border border-border p-8 text-center">
          <p className="text-muted text-sm">No tradable windows report. Click Refresh to generate.</p>
        </div>
      )}
    </div>
  );
}
