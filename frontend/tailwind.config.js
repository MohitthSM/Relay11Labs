/** @type {import('tailwindcss').Config} */
// Control-room palette: near-black panels, phosphor amber for "on air", green/red/cyan for state.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { 950: "#05070a", 900: "#090c11", 800: "#0e131a", 700: "#141b25", 600: "#1d2634", 500: "#2a3547" },
        dim: "#6c7b8f",
        phosphor: "#ffb020", // on air
        ok: "#38e08c", // approved, passed, live
        alert: "#ff4d5a", // waiting for an operator, rejected
        info: "#4cc3ff", // scheduled
      },
      fontFamily: {
        mono: ['ui-monospace', '"JetBrains Mono"', '"SF Mono"', "Menlo", "Consolas", "monospace"],
        sans: ["ui-sans-serif", "system-ui", '"Segoe UI"', "Roboto", "sans-serif"],
      },
      keyframes: {
        glow: { "0%, 100%": { opacity: "0.55" }, "50%": { opacity: "1" } },
        blink: { "0%, 49%": { opacity: "1" }, "50%, 100%": { opacity: "0.2" } },
        ring: { "0%": { opacity: "0.9" }, "100%": { opacity: "0" } },
        slidein: { from: { transform: "translateX(24px)", opacity: "0" }, to: { transform: "none", opacity: "1" } },
      },
      animation: {
        glow: "glow 1.4s ease-in-out infinite",
        blink: "blink 1s steps(1) infinite",
        ring: "ring 1.2s ease-out infinite",
        slidein: "slidein 160ms ease-out",
      },
    },
  },
  plugins: [],
};
