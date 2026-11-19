/** iOS-style segmented control: a quiet track with the selected segment raised in gold. */
export default function Segmented<T extends string>({
  value, options, onChange, label,
}: {
  value: T;
  options: { key: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex items-center gap-0.5 p-0.5 rounded-full bg-bg border border-border">
      {options.map(o => {
        const on = o.key === value;
        return (
          <button
            key={o.key}
            role="radio"
            aria-checked={on}
            onClick={() => onChange(o.key)}
            className={`pressable h-7 px-3.5 rounded-full text-xs font-semibold whitespace-nowrap
              ${on ? 'btn-gold' : 'text-ink2 hover:text-ink hover:bg-white/80'}`}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
