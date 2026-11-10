/** Theme tokens copied from the CTO's demo (src/theme.css). */
const v = (n) => `var(--${n})`;
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        paper: v('paper'), surface: { DEFAULT: v('surface'), 2: v('surface-2'), 3: v('surface-3') },
        line: { DEFAULT: v('line'), strong: v('line-strong') },
        ink: { DEFAULT: v('ink'), 2: v('ink-2'), 3: v('ink-3') },
        accent: { DEFAULT: v('accent'), hover: v('accent-hover'), soft: v('accent-soft'), line: v('accent-line') },
        sky: v('brand-sky'),
        rail: { DEFAULT: v('rail'), ink: v('rail-ink'), 'ink-2': v('rail-ink-2'), line: v('rail-line'), hover: v('rail-hover') },
        good: { DEFAULT: v('good'), soft: v('good-soft') },
        warn: { DEFAULT: v('warn'), soft: v('warn-soft') },
        crit: { DEFAULT: v('crit'), soft: v('crit-soft') },
      },
      fontFamily: {
        sans: ['IBM Plex Sans', 'system-ui', 'sans-serif'],
        mono: ['IBM Plex Mono', 'ui-monospace', 'monospace'],
        serif: ['EB Garamond', 'Georgia', 'serif'],
      },
      fontSize: { base: ['14px', '20px'], md: ['16px', '22px'], lg: ['18px', '24px'] },
      boxShadow: { pop: v('shadow-pop'), soft: v('shadow-soft') },
    },
  },
};
