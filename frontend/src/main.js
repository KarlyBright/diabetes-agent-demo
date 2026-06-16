import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './style.css'
import App from './App.vue'
import { router } from './router'

const isBrowserExtensionError = (reason) => {
  if (!reason) {
    return false
  }

  const stack = typeof reason?.stack === 'string' ? reason.stack : ''
  const message = typeof reason?.message === 'string' ? reason.message : ''
  const directUrl = typeof reason?.filename === 'string' ? reason.filename : ''
  const originalStack = typeof reason?.originalError?.stack === 'string' ? reason.originalError.stack : ''

  return [stack, message, directUrl, originalStack].some((value) => value.includes('chrome-extension://'))
}

window.addEventListener('unhandledrejection', (event) => {
  if (isBrowserExtensionError(event.reason)) {
    event.preventDefault()
  }
})

window.addEventListener('error', (event) => {
  if (isBrowserExtensionError(event.error) || isBrowserExtensionError({ filename: event.filename })) {
    event.preventDefault()
  }
})

createApp(App).use(createPinia()).use(router).use(ElementPlus).mount('#app')
