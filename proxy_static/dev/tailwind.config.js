/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "../index.html",
    "../app.js"
  ],
  darkMode: ['selector', '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        // CSS 变量映射（保持现有颜色系统）
        canvas: 'var(--canvas)',
        surface: {
          DEFAULT: 'var(--surface)',
          soft: 'var(--surface-soft)'
        },
        line: {
          DEFAULT: 'var(--line)',
          strong: 'var(--line-strong)'
        },
        text: 'var(--text)',
        muted: 'var(--muted)',

        // 主题色
        teal: {
          DEFAULT: 'var(--teal)',
          soft: 'var(--teal-soft)',
          dark: '#0f5c62',
          hover: '#0d4f54'
        },
        green: {
          DEFAULT: 'var(--green)',
          soft: 'var(--green-soft)'
        },
        amber: {
          DEFAULT: 'var(--amber)',
          soft: 'var(--amber-soft)'
        },
        red: {
          DEFAULT: 'var(--red)'
        },

        // 特殊用途色
        'row-hover': 'var(--row-hover)',
        'current-border': 'var(--current-border)',
        'recovery-border': 'var(--recovery-border)',
        'recovery-text': 'var(--recovery-text)',
        'warning-border': 'var(--warning-border)',
        'warning-text': 'var(--warning-text)',
        'danger-soft': 'var(--danger-soft)',
        'danger-border': 'var(--danger-border)',
        'danger-text': 'var(--danger-text)'
      },
      spacing: {
        '4.5': '1.125rem',
        '18': '4.5rem'
      },
      fontSize: {
        '2xs': ['0.625rem', { lineHeight: '1' }],
      },
      borderRadius: {
        'DEFAULT': '6px',
      },
      boxShadow: {
        'app': 'var(--shadow)'
      },
      transitionDuration: {
        '140': '140ms',
        '160': '160ms'
      },
      fontFamily: {
        'sans': ['"Segoe UI"', '"Microsoft YaHei UI"', 'sans-serif'],
        'mono': ['"Cascadia Code"', 'Consolas', 'monospace']
      }
    }
  },
  plugins: []
}
