<template>
  <div class="auth">
    <!-- 左：品牌区（纯 CSS + 文案，不引图片资源） -->
    <section class="auth__brand">
      <div class="auth__logo">L</div>
      <h1>LawAnswer</h1>
      <p class="auth__slogan">面向法律条文的多路 RAG 问答</p>
      <ul class="auth__points">
        <li><b>双源路由</b>高频问答库秒回，法条检索兜底</li>
        <li><b>逐字流式</b>边生成边阅读，回答带法条引用</li>
        <li><b>可溯源</b>每条结论标注《法律名》条号，可展开原文</li>
      </ul>
      <p class="auth__foot">劳动法 · 劳动合同法 · 民法典</p>
    </section>

    <!-- 右：登录表单 -->
    <section class="auth__panel">
      <form class="auth__form card fade-up" @submit.prevent="submit">
        <h2>登录</h2>
        <p class="muted auth__sub">使用账号登录后开始咨询</p>

        <div class="field">
          <label class="field__label" for="login-username">账号</label>
          <input
            id="login-username"
            v-model.trim="form.username"
            class="input"
            :class="{'input--error': errors.username}"
            autocomplete="username"
            placeholder="字母、数字或下划线"
            @input="errors.username = ''"
          />
          <p v-if="errors.username" class="field__error">{{ errors.username }}</p>
        </div>

        <div class="field">
          <label class="field__label" for="login-password">密码</label>
          <div class="auth__pw">
            <input
              id="login-password"
              v-model="form.password"
              class="input"
              :class="{'input--error': errors.password}"
              :type="showPw ? 'text' : 'password'"
              autocomplete="current-password"
              placeholder="请输入密码"
              @input="errors.password = ''"
            />
            <button type="button" class="auth__eye" :aria-label="showPw ? '隐藏密码' : '显示密码'" @click="showPw = !showPw">
              {{ showPw ? '🙈' : '👁' }}
            </button>
          </div>
          <p v-if="errors.password" class="field__error">{{ errors.password }}</p>
        </div>

        <div class="field">
          <label class="field__label">验证码</label>
          <CaptchaField
            v-model="form.captcha_code"
            :image="captcha.image"
            :error="errors.captcha"
            @refresh="loadCaptcha"
          />
          <p v-if="errors.captcha" class="field__error">{{ errors.captcha }}</p>
        </div>

        <button class="btn btn--block" type="submit" :disabled="loading">
          {{ loading ? '登录中…' : '登录' }}
        </button>

        <p class="auth__switch muted">
          还没有账号？<router-link to="/register">立即注册</router-link>
        </p>
      </form>
    </section>
  </div>
</template>

<script setup lang="ts">
import {onMounted, reactive, ref} from 'vue'
import {useRoute, useRouter} from 'vue-router'

import CaptchaField from '@/components/CaptchaField.vue'
import {fetchCaptcha} from '@/api/auth'
import {ApiError, errorText} from '@/api/http'
import {useAuthStore} from '@/stores/auth'
import {useToast} from '@/composables/useToast'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const toast = useToast()

const form = reactive({username: '', password: '', captcha_id: '', captcha_code: ''})
const captcha = reactive({image: '', id: ''})
const errors = reactive({username: '', password: '', captcha: ''})
const loading = ref(false)
const showPw = ref(false)

async function loadCaptcha() {
  form.captcha_code = ''
  try {
    const data = await fetchCaptcha()
    captcha.id = data.captcha_id
    captcha.image = data.image
    form.captcha_id = data.captcha_id
  } catch (err) {
    captcha.image = ''
    toast.error(errorText(err))
  }
}

async function submit() {
  errors.username = form.username ? '' : '请输入账号'
  errors.password = form.password ? '' : '请输入密码'
  errors.captcha = form.captcha_code.length === 4 ? '' : '请输入 4 位验证码'
  if (errors.username || errors.password || errors.captcha) return

  loading.value = true
  try {
    await auth.login({...form})
    toast.success('登录成功')
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/chat'
    router.replace(redirect)
  } catch (err) {
    const apiErr = err instanceof ApiError ? err : null
    if (apiErr?.code === 'captcha_invalid') {
      errors.captcha = apiErr.message
      await loadCaptcha() // 验证码错误 → 自动刷新（§4.2 P1）
    } else if (apiErr?.status === 429) {
      errors.captcha = apiErr.message
      await loadCaptcha()
    } else {
      toast.error(errorText(err))
      await loadCaptcha()
    }
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  // 注册页跳转过来时回填账号（§4.2 P2）
  const prefill = route.query.username
  if (typeof prefill === 'string') form.username = prefill
  await loadCaptcha()
})
</script>

<style scoped>
.auth { display: flex; min-height: 100vh; background: var(--c-bg); }

/* 左侧品牌区：深靛蓝底 + 克制的径向光 */
.auth__brand {
  flex: 1 1 52%;
  display: flex; flex-direction: column; justify-content: center;
  gap: 10px; padding: 64px 56px;
  color: #EAF0FF;
  background:
    radial-gradient(1200px 500px at 20% 0%, rgba(43, 74, 203, .55), transparent 60%),
    linear-gradient(160deg, #16204A 0%, #0F1734 100%);
}
.auth__logo {
  display: grid; place-items: center;
  width: 44px; height: 44px; border-radius: 13px;
  background: #fff; color: var(--c-primary);
  font-weight: 800; font-size: 20px; letter-spacing: -.02em;
}
.auth__brand h1 { font-size: 30px; margin-top: 8px; letter-spacing: -.02em; }
.auth__slogan { margin: 0; color: #A9B6E6; font-size: 14px; }
.auth__points { list-style: none; margin: 26px 0 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
.auth__points li { position: relative; padding-left: 16px; font-size: 13.5px; color: #C9D3F5; line-height: 1.6; }
.auth__points li::before {
  content: ''; position: absolute; left: 0; top: 8px;
  width: 6px; height: 6px; border-radius: 2px; background: var(--c-accent);
}
.auth__points b { color: #fff; }
.auth__foot { margin-top: 34px; font-size: 12px; color: #7C8BC4; letter-spacing: .04em; }

/* 右侧表单 */
.auth__panel { flex: 1 1 48%; display: grid; place-items: center; padding: 32px; }
.auth__form { width: min(400px, 100%); padding: 28px 26px 22px; }
.auth__form h2 { font-size: 22px; }
.auth__sub { margin: 4px 0 20px; font-size: 13px; }
.auth__pw { position: relative; }
.auth__pw .input { padding-right: 42px; }
.auth__eye {
  position: absolute; right: 6px; top: 50%; transform: translateY(-50%);
  width: 30px; height: 30px; border: none; background: transparent;
  cursor: pointer; font-size: 14px; opacity: .65;
}
.auth__eye:hover { opacity: 1; }
.auth__switch { margin: 14px 0 0; font-size: 13px; text-align: center; }

@media (max-width: 900px) {
  .auth__brand { display: none; }
  .auth__panel { flex: 1 1 100%; padding: 20px; }
}
</style>
