import type { Config } from "tailwindcss";

const token = (name: string) => `hsl(var(--${name}) / <alpha-value>)`;

const config: Config = {
  darkMode: ["class"],
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    container: { center: true, padding: "1rem" },
    extend: {
      fontFamily: {
        sans: ["var(--font-geist-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-geist-mono)", "ui-monospace", "monospace"],
      },
      colors: {
        background: token("background"),
        foreground: token("foreground"),
        surface: token("surface"),
        "surface-2": token("surface-2"),
        border: token("border"),
        input: token("input"),
        ring: token("ring"),
        muted: { DEFAULT: token("muted"), foreground: token("muted-foreground") },
        subtle: token("subtle"),
        primary: { DEFAULT: token("primary"), foreground: token("primary-foreground"), soft: token("primary-soft") },
        ai: { DEFAULT: token("ai"), soft: token("ai-soft") },
        destructive: { DEFAULT: token("destructive"), foreground: token("destructive-foreground") },
        // data-viz roles (validated reference palette)
        series: { 1: "var(--series-1)", track: "var(--series-track)" },
        status: { good: "var(--status-good)", warning: "var(--status-warning)", serious: "var(--status-serious)", critical: "var(--status-critical)" },
      },
      borderRadius: { lg: "0.75rem", md: "0.5rem", sm: "0.375rem", xl: "1rem" },
      boxShadow: {
        card: "0 1px 2px 0 rgb(0 0 0 / 0.04), 0 1px 3px 0 rgb(0 0 0 / 0.04)",
        pop: "0 12px 32px -8px rgb(15 12 40 / 0.22), 0 2px 6px rgb(15 12 40 / 0.08)",
      },
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "zoom-in": { from: { opacity: "0", transform: "translate(-50%, -48%) scale(0.97)" }, to: { opacity: "1", transform: "translate(-50%, -50%) scale(1)" } },
        "slide-up": { from: { opacity: "0", transform: "translateY(6px)" }, to: { opacity: "1", transform: "translateY(0)" } },
        shimmer: { "0%": { backgroundPosition: "-200% 0" }, "100%": { backgroundPosition: "200% 0" } },
      },
      animation: {
        "fade-in": "fade-in 150ms ease-out",
        "zoom-in": "zoom-in 180ms cubic-bezier(0.16, 1, 0.3, 1)",
        "slide-up": "slide-up 240ms cubic-bezier(0.16, 1, 0.3, 1) both",
        shimmer: "shimmer 2.4s linear infinite",
      },
    },
  },
  plugins: [],
};

export default config;
