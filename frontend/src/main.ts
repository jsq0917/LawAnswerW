import {createApp} from 'vue'
import {createPinia} from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'

import App from './App.vue'
import router from './router'
import './styles/tokens.css'
import './styles/base.css'
import './styles/markdown.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)
// 只用于表单/弹窗/上传/滑块；中文 locale 保证日期与提示是中文（§6 验收：页签中文）
app.use(ElementPlus, {locale: zhCn})
app.mount('#app')
