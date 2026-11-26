import { useLayoutEffect, useRef, useState } from 'react';

/**
 * iOS-style segmented control: a recessed glass track with a raised clear-glass
 * thumb that slides to the chosen segment (the label turns SPVH blue).
 */
export default function Segmented<T extends string>({
  value, options, onChange, label,
}: {
  value: T;
  options: { key: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
}) {
  const track = useRef<HTMLDivElement>(null);
  const [thumb, setThumb] = useState<{ x: number; w: number } | null>(null);

  useLayoutEffect(() => {
    const el = track.current; if (!el) return;
    const measure = () => {
      const b = el.querySelector<HTMLButtonElement>('[aria-checked="true"]');
      setThumb(b ? { x: b.offsetLeft, w: b.offsetWidth } : null);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [value, options.length]);

  return (
    <div ref={track} role="radiogroup" aria-label={label}
         className="glass-track scroll-x relative inline-flex max-w-full items-center gap-0.5 p-[3px] rounded-full">
      {thumb && (
        <span aria-hidden className="glass-thumb absolute top-[3px] bottom-[3px] left-0 rounded-full"
              style={{ width: thumb.w, transform: `translateX(${thumb.x}px)` }} />
      )}
      {options.map(o => {
        const on = o.key === value;
        return (
          <button
            key={o.key}
            role="radio"
            aria-checked={on}
            onClick={() => onChange(o.key)}
            className={`pressable relative shrink-0 h-9 md:h-7 px-3.5 rounded-full text-xs whitespace-nowrap
              ${on ? 'text-accent font-bold' : 'text-ink2 font-semibold hover:text-ink'}`}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
