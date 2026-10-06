import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import { resolve } from 'path'

export default defineConfig(({ command }) => ({
  base: command === 'serve' ? '/' : '',
  plugins: [vue(), tailwindcss()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src')
    }
  },
  build: {
    outDir: 'dist',
    assetsDir: 'static/assets',
    sourcemap: false,
    minify: 'esbuild',
    rollupOptions: {
      input: {
        index: resolve(__dirname, 'index.html'),
        'classic-features': resolve(__dirname, 'src/classic-features.js')
      },
      output: {
        entryFileNames: chunk => chunk.name === 'classic-features' ? 'static/assets/classic-features.js' : 'static/assets/[name]-[hash].js',
        assetFileNames: asset => asset.name === 'classic-features.css' ? 'static/assets/classic-features.css' : 'static/assets/[name]-[hash][extname]',
        manualChunks: undefined
      }
    }
  },
  server: {
    port: 3000,
    strictPort: false,
    proxy: {
      '/control': {
        target: 'http://127.0.0.1:17890',
        changeOrigin: true
      }
    }
  }
}))
