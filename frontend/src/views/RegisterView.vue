<template>
  <div class="auth">
    <section class="auth__brand">
      <div class="auth__logo">L</div>
      <h1>创建账号</h1>
      <p class="auth__slogan">三步开始：注册 → 提问 → 得到带法条引用的答案</p>
      <ul class="auth__points">
        <li>账号 3–32 位字母、数字或下划线</li>
        <li>密码至少 8 位，需同时包含字母与数字</li>
        <li>注册后可上传文档、管理知识库</li>
      </ul>
    </section>

    <section class="auth__panel">
      <form class="auth__form card fade-up" @submit.prevent="submit">
        <h2>注册</h2>
        <p class="muted auth__sub">填写以下信息创建账号</p>

        <div class="field">
          <label class="field__label" for="reg-username">账号</label>
          <input
            id="reg-username"
            v-model.trim="form.username"
            class="input"
            :class="{'input--error': !!nameError}"
            autocomplete="username"
            placeholder="3–32 位字母、数字或下划线"
            @blur="checkName"
            @input="onNameInput"
          />
          <p v-if="nameError" class="field__error">{{ nameError }}</p>
          <p v-else-if="nameOk" class="field__hint" style="color: #0B8F68">账号可用</p>
        </div>

        <div class="field">
          <label class="field__label" for="reg-password">密码</label>
          <div class="auth__pw">
            <input
              id="reg-password"
              v-model="form.password"
              class="input"
              :class="{'input--error': !!pwError}"
              :type="showPw ? 'text' : 'password'"
              autocomplete="new-password"
              placeholder="至少 8 位，含字母和数字"
              @input="onPwInput"
            />
            <button type="button" class="auth__eye" @click="showPw = !showPw">{{ showPw ? '🙈' : '👁' }}</button>
          </div>
          <p v-if="pwError" class="field__error">{{ pwError }}</p>
        </div>

        <div class="field">
          <label class="field__label" for="reg-confirm">确认密码</label>
          <input
            id="reg-confirm"
            v-model="form.confirm"
            class="input"
            :class="{'input--error': !!confirmError}"
            :type="showPw ? 'text' : 'password'"
            autocomplete="new-password"
            placeholder="再次输入密码"
            @input="onConfirmInput"
          />
          <p v-if="confirmError" class="field__error">{{ confirmError }}</p>
        </div>

        <div class="field">
          <label class="field__label">验证码</label>
          <CaptchaField v-model="form.captcha_code" :image="captcha.image" :error="captchaError" @refresh="loadCaptcha"/>
          <p v-if="captchaError" class="field__error">{{ captchaError }}</p>
        </div>

        <button class="btn btn--block" type="submit" :disabled="!canSubmit || loading">
          {{ loading ? '注册中…' : '注册' }}
        </button>

        <p class="auth__switch muted">
          已有账号？<router-link to="/login">去登录</router-link>
        </p>
      </form>
    </section>
  </div>
</template>

<script setup lang="ts">
import {computed, onMounted, reactive, ref} from 'vue'
import {useRouter} from 'vue-router'

import CaptchaField from '@/components/CaptchaField.vue'
import {checkUsername, fetchCaptcha} from '@/api/auth'
import {ApiError, errorText} from '@/api/http'
import {useAuthStore} from '@/stores/auth'
import {useToast} from '@/composables/useToast'

const router = useRouter()
const auth = useAuthStore()
const toast = useToast()

const form = reactive({username: '', password: '', confirm: '', captcha_id: '', captcha_code: ''})
const captcha = reactive({image: ''})
const loading = ref(false)
const showPw = ref(false)

const nameError = ref('')
const nameOk = ref(false)
const pwError = ref('')
const confirmError = ref('')
const captchaError = ref('')

const USERNAME_RE = /^[A-Za-z0-9_]{3,32}$/

const canSubmit = computed(
  () =>
    USERNAME_RE.test(form.username) &&
    form.password.length >= 8 &&
    form.password === form.confirm &&
    form.captcha_code.length === 4,
)

async function loadCaptcha() {
  form.captcha_code = ''
  try {
    const data = await fetchCaptcha()
    captcha.image = data.image
    form.captcha_id = data.captcha_id
  } catch (err) {
    captcha.image = ''
    toast.error(errorText(err))
  }
}

function onNameInput() {
  nameOk.value = false
  nameError.value = USERNAME_RE.test(form.username) ? '' : '账号需为 3–32 位字母、数字或下划线'
}

/** 失焦实时查重（§4.2 P2） */
async function checkName() {
  onNameInput()
  if (nameError.value) return
  try {
    const res = await checkUsername(form.username)
    if (res.available) {
      nameOk.value = true
      nameError.value = ''
    } else {
      nameOk.value = false
      nameError.value = res.reason || '账号不可用'
    }
  } catch {
    /* 查重失败不阻塞注册，提交时后端会再校验 */
  }
}

function onPwInput() {
  const p = form.password
  if (!p) pwError.value = ''
  else if (p.length < 8) pwError.value = '密码至少 8 位'
  else if (!(/[A-Za-z]/.test(p) && /\d/.test(p))) pwError.value = '密码需同时包含字母和数字'
  else pwError.value = ''
  onConfirmInput()
}

function onConfirmInput() {
  confirmError.value =
    form.confirm && form.confirm !== form.password ? '两次输入的密码不一致' : ''
}

async function submit() {
  captchaError.value = ''
  if (!canSubmit.value) return
  loading.value = true
  try {
    await auth.register({
      username: form.username,
      password: form.password,
      captcha_id: form.captcha_id,
      captcha_code: form.captcha_code,
    })
    toast.success('注册成功，请登录')
    router.replace({name: 'login', query: {username: form.username}})
  } catch (err) {
    const apiErr = err instanceof ApiError ? err : null
    if (apiErr?.code === 'captcha_invalid') {
      captchaError.value = apiErr.message
      await loadCaptcha()
    } else if (apiErr?.code === 'conflict') {
      nameError.value = apiErr.message
    } else {
      toast.error(errorText(err))
      await loadCaptcha()
    }
  } finally {
    loading.value = false
  }
}

onMounted(loadCaptcha)
</script>

<style scoped>
.auth { display: flex; min-height: 100vh; background: var(--c-bg); }
.auth__brand {
  flex: 1 1 52%;
  display: flex; flex-direction: column; justify-content: center; gap: 10px;
  padding: 64px 56px; color: #EAF0FF;
  background:
    radial-gradient(1200px 500px at 20% 0%, rgba(43, 74, 203, .55), transparent 60%),
    linear-gradient(160deg, #16204A 0%, #0F1734 100%);
}
.auth__logo {
  display: grid; place-items: center; width: 44px; height: 44px; border-radius: 13px;
  background: #fff; color: var(--c-primary); font-weight: 800; font-size: 20px;
}
.auth__brand h1 { font-size: 30px; margin-top: 8px; }
.auth__slogan { margin: 0; color: #A9B6E6; font-size: 14px; }
.auth__points { list-style: none; margin: 26px 0 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
.auth__points li { position: relative; padding-left: 16px; font-size: 13.5px; color: #C9D3F5; line-height: 1.6; }
.auth__points li::before {
  content: ''; position: absolute; left: 0; top: 8px;
  width: 6px; height: 6px; border-radius: 2px; background: var(--c-accent);
}
.auth__panel { flex: 1 1 48%; display: grid; place-items: center; padding: 32px; }
.auth__form { width: min(420px, 100%); padding: 28px 26px 22px; }
.auth__form h2 { font-size: 22px; }
.auth__sub { margin: 4px 0 20px; font-size: 13px; }
.auth__pw { position: relative; }
.auth__pw .input { padding-right: 42px; }
.auth__eye {
  position: absolute; right: 6px; top: 50%; transform: translateY(-50%);
  width: 30px; height: 30px; border: none; background: transparent; cursor: pointer; opacity: .65;
}
.auth__switch { margin: 14px 0 0; font-size: 13px; text-align: center; }

@media (max-width: 900px) {
  .auth__brand { display: none; }
  .auth__panel { flex: 1 1 100%; padding: 20px; }
}
</style>
