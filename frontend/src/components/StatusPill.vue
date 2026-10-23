<template>
  <span class="pill" :class="cls">
    <i v-if="pulsing" class="pill__dot"/>
    {{ text }}
  </span>
</template>

<script setup lang="ts">
/** 文档状态胶囊（§4.2 P6）：pending 灰 / parsing 蓝闪 / ready 绿 / failed 红。 */
import {computed} from 'vue'

const props = defineProps<{status: string}>()

const MAP: Record<string, {text: string; cls: string; pulsing?: boolean}> = {
  pending: {text: '排队中', cls: ''},
  parsing: {text: '解析中', cls: 'pill--info pill--pulse', pulsing: true},
  ready: {text: '就绪', cls: 'pill--ok'},
  failed: {text: '失败', cls: 'pill--danger'},
}

const meta = computed(() => MAP[props.status] || {text: props.status, cls: ''})
const text = computed(() => meta.value.text)
const cls = computed(() => meta.value.cls)
const pulsing = computed(() => !!meta.value.pulsing)
</script>

<style scoped>
.pill__dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: currentColor; opacity: .8;
}
</style>
