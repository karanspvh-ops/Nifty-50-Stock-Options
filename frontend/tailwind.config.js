/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        bg:      "#0f172a",
        surface: "#1e293b",
        border:  "#334155",
        muted:   "#64748b",
        up:      "#22c55e",
        down:    "#ef4444",
        accent:  "#3b82f6",
      },
    },
  },
  plugins: [],
};

