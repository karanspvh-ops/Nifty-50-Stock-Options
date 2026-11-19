/** @type {import('tailwindcss').Config} */
// Colours are CSS variables (space-separated RGB channels) defined in
// src/index.css, so every token supports Tailwind's /opacity modifier.
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas:  v("canvas"),     // page
        bg:      v("inset"),      // wells, inputs, table heads
        surface: v("surface"),    // glass panels (see .bg-surface in index.css)
        border:  v("line"),
        ink:     v("ink"),        // primary text
        ink2:    v("ink-2"),      // secondary text
        muted:   v("muted"),      // labels, captions (6:1 on white)
        faint:   v("faint"),      // disabled only
        up:      v("up"),
        down:    v("down"),
        warn:    v("warn"),
        accent:  v("gold-700"),   // gold text / borders on white (5.3:1)
        brand:   v("brand"),      // SPVH blue (logo, ES identity)
        teal:    v("teal"),       // SPVH teal (OB identity)
        gold: {
          50:  v("gold-50"),
          100: v("gold-100"),
          300: v("gold-300"),
          400: v("gold-400"),
          500: v("gold-500"),
          600: v("gold-600"),
          700: v("gold-700"),
          DEFAULT: v("gold-500"),
        },
      },
      // Calibri sets ~1px smaller than Segoe UI at the same size, so the
      // scale is nudged up to keep dense tables readable.
      fontSize: {
        xs:   ["13px",   { lineHeight: "18px" }],
        sm:   ["14.5px", { lineHeight: "20px" }],
        base: ["16px",   { lineHeight: "24px" }],
        lg:   ["18px",   { lineHeight: "26px" }],
        xl:   ["21px",   { lineHeight: "28px" }],
        "2xl":["26px",   { lineHeight: "32px" }],
      },
      fontFamily: {
        sans: ["Calibri", "Carlito", "'Segoe UI'", "system-ui", "sans-serif"],
        mono: ["'Cascadia Mono'", "Consolas", "ui-monospace", "monospace"],
      },
      borderRadius: {
        panel: "18px",
        control: "10px",
      },
      boxShadow: {
        glass: "0 1px 0 rgb(255 255 255 / 0.9) inset, 0 0 0 1px rgb(22 24 29 / 0.06), 0 10px 30px -12px rgb(76 58 18 / 0.14)",
        lift:  "0 1px 0 rgb(255 255 255 / 0.9) inset, 0 0 0 1px rgb(22 24 29 / 0.07), 0 18px 40px -16px rgb(76 58 18 / 0.22)",
        gold:  "0 1px 0 rgb(255 255 255 / 0.55) inset, 0 6px 16px -6px rgb(168 124 34 / 0.55)",
      },
      transitionTimingFunction: {
        out: "cubic-bezier(0.23, 1, 0.32, 1)",
      },
    },
  },
  plugins: [],
};
