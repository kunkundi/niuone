import { createApp } from 'vue'
import App from './App.vue'
import router from './router.js'
import './styles.css'

document.documentElement.dataset.frontend = 'vue3-vite'
createApp(App).use(router).mount('#app')
