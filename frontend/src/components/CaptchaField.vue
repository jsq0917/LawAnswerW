<template>
  <div class="captcha">
    <input
      class="input"
      :class="{'input--error': !!error}"
      :value="modelValue"
      maxlength="4"
      inputmode="numeric"
      autocomplete="off"
      placeholder="4 位验证码"
      @input="onInput"
    />
    <button
      class="captcha__img"
      :class="{'captcha__img--retry': retryIn > 0}"
      type="button"
      :title="retryIn > 0 ? '验证码服务暂不可用，点击立即重试' : '点击刷新验证码'"
      @click="$emit('refresh')"
    >
      <img v-if="image" :src="image" alt="图形验证码"/>
      <span v-else-if="retryIn > 0" class="captcha__hint">{{ retryIn }}s 后重试</span>
      <span v-else-if="loading" class="faint">加载中…</span>
      <span v-else class="captcha__hint">点击重试</span>
    </button>
  </div>
</template>

<script setup lang="ts">
/** 图形验证码：服务端出 SVG（后端 app/core/captcha.py），点击刷新。
 *
 * `retryIn` 由 `useCaptcha` 驱动：服务端不可用时显示"Ns 后重试"倒计时，
 * 用户也可以直接点按钮立刻重试——不要让人对着"加载中…"干等。
 */
withDefaults(
  defineProps<{
    modelValue: string
    image: string
    error?: string | null
    retryIn?: number
    loading?: boolean
  }>(),
  {error: null, retryIn: 0, loading: false},
)
const emit = defineEmits<{(e: 'update:modelValue', v: string): void; (e: 'refresh'): void}>()

function onInput(e: Event) {
  const el = e.target as HTMLInputElement
  emit('update:modelValue', el.value.replace(/\D/g, '').slice(0, 4))
}
</script>

<style scoped>
.captcha { display: flex; gap: 8px; align-items: center; }
.captcha .input { flex: 1; letter-spacing: 4px; font-family: var(--font-mono); }
.captcha__img {
  flex: 0 0 120px; height: 40px; padding: 0;
  border: 1px solid var(--c-border-strong); border-radius: 10px;
  background: #F2F5FC; cursor: pointer; overflow: hidden;
  display: grid; place-items: center;
  transition: border-color var(--dur) var(--ease);
}
.captcha__img:hover { border-color: var(--c-primary); }
.captcha__img img { width: 120px; height: 40px; display: block; }
.captcha__img--retry { border-color: var(--c-warn, #B8860B); background: #FFF8E6; }
.captcha__hint { font-size: 11.5px; line-height: 1.2; text-align: center; color: #8A6D1F; }
</style>
