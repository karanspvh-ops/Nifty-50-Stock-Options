/** @type {import('tailwindcss').Config} */
// Colours are CSS variables (space-separated RGB channels) defined in
// src/index.css, so every token supports Tailwind's /opacity modifier.
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  // hover: styles only on devices that can hover, so a tapped button on a
  // phone doesn't stay stuck in its hover colour
  future: { hoverOnlyWhenSupported: true },
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
        accent:  v("spvh-600"),   // SPVH blue text / borders on white (7.0:1)
        brand:   v("brand"),      // SPVH logo blue
        es:      v("es"),         // Early Scalp identity (violet)
        teal:    v("teal"),       // SPVH teal (OB identity)
        spvh: {
          50:  v("spvh-50"),
          100: v("spvh-100"),
          300: v("spvh-300"),
          400: v("spvh-400"),
          500: v("spvh-500"),
          600: v("spvh-600"),
          700: v("spvh-700"),
          DEFAULT: v("spvh-500"),
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
        glass: "0 1px 0 rgb(255 255 255 / 0.9) inset, 0 0 0 1px rgb(22 24 29 / 0.06), 0 10px 30px -12px rgb(30 50 90 / 0.12)",
        lift:  "0 1px 0 rgb(255 255 255 / 0.9) inset, 0 0 0 1px rgb(22 24 29 / 0.07), 0 18px 40px -16px rgb(30 50 90 / 0.2)",
        primary: "0 1px 0 rgb(255 255 255 / 0.25) inset, 0 6px 16px -6px rgb(52 88 154 / 0.5)",
      },
      transitionTimingFunction: {
        out: "cubic-bezier(0.23, 1, 0.32, 1)",
      },
    },
  },
  plugins: [],
};
