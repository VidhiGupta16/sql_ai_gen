import type { Config } from 'tailwindcss';

export default {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: 'hsl(var(--background))',
        fg: 'hsl(var(--foreground))',
        card: 'hsl(var(--card))',
        border: 'hsl(var(--border))',
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        emerald: '#7BAE7F',
        amber: '#C8A96B',
        danger: '#C46F6F',
        surface: '#171A21',
        panel: '#1E222B',
      },
      boxShadow: {
        glow: '0 0 0 rgba(0, 0, 0, 0)',
        glass: '0 18px 40px rgba(0, 0, 0, 0.22)',
        soft: '0 10px 24px rgba(0, 0, 0, 0.12)',
      },
      backgroundImage: {
        'hero-gradient':
          'radial-gradient(circle at top left, rgba(123,174,127,0.10), transparent 28%), radial-gradient(circle at top right, rgba(200,169,107,0.08), transparent 30%), linear-gradient(135deg, rgba(15,17,21,1), rgba(13,15,19,1))',
      },
    },
  },
  plugins: [],
} satisfies Config;
