import { useEffect, useState } from 'react';

const fmt = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
});

/**
 * India time, ticking every second on its own -- independent of the price
 * feed, so it runs before 9:15, between ticks, and with the office PC off.
 * Always IST, whatever time zone the viewer's device is in. Re-renders only
 * itself, never the header around it.
 */
export default function IstClock({ className = '' }: { className?: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    // Align to the second boundary so the display flips with the real clock
    let id: ReturnType<typeof setInterval> | undefined;
    const t = setTimeout(() => {
      setNow(Date.now());
      id = setInterval(() => setNow(Date.now()), 1000);
    }, 1000 - (Date.now() % 1000));
    return () => { clearTimeout(t); if (id) clearInterval(id); };
  }, []);
  return (
    <span className={`tabular-nums ${className}`} title="India Standard Time">
      {fmt.format(now)} IST
    </span>
  );
}
