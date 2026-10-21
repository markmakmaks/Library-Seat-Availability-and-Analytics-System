// Tailwind Play CDN configuration (loaded right after the Tailwind CDN script).
tailwind.config = {
  theme: {
    extend: {
      colors: {
        navy: {
          50: '#f0f4f8',
          100: '#d9e2ec',
          500: '#334e68',
          800: '#102a43',
          900: '#0b1b2b',
        },
        royal: {
          500: '#2563eb',
          600: '#1d4ed8',
          700: '#1e40af',
        },
        sky: {
          100: '#e0f2fe',
          400: '#38bdf8',
          500: '#0ea5e9',
        }
      },
      fontFamily: {
        sans: ['Inter', 'sans-serif'],
      }
    }
  }
}
