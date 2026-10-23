import {defineConfig} from 'vite'
import vue from '@vitejs/plugin-vue'
import {fileURLToPath, URL} from 'node:url'

// 开发端口 5180（5173 已被本机其他项目占用，见设计方案 §1.5）
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {'@': fileURLToPath(new URL('./src', import.meta.url))},
  },
  server: {
    port: 5180,
    proxy: {
      // 前端只认 /api，后端跑在 8101（8000/8100 在本机无法 bind）
      '/api': {target: 'http://127.0.0.1:8101', changeOrigin: true},
    },
  },
  build: {
    // Element Plus 体积大，单独成 chunk：首屏（登录页）不必等组件库
    rollupOptions: {
      output: {
        manualChunks: {
          'element-plus': ['element-plus'],
          vendor: ['vue', 'vue-router', 'pinia', 'axios', 'marked', 'dompurify'],
        },
      },
    },
    chunkSizeWarningLimit: 900,
  },
})
