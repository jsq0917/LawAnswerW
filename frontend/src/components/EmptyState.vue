<template>
  <div class="empty fade-up">
    <div class="empty__art" v-html="art"/>
    <h3 class="empty__title">{{ title }}</h3>
    <p class="empty__desc">{{ description }}</p>
    <slot/>
  </div>
</template>

<script setup lang="ts">
defineProps<{title: string; description?: string}>()

// 插画级空态（§4.1）：轻量 SVG，不引外部资源
const art = `<svg width="96" height="96" viewBox="0 0 96 96" fill="none">
  <rect x="18" y="14" width="60" height="70" rx="10" fill="#EDF1FF"/>
  <rect x="28" y="26" width="30" height="5" rx="2.5" fill="#BEC8F1"/>
  <rect x="28" y="38" width="40" height="5" rx="2.5" fill="#D6DCF6"/>
  <rect x="28" y="50" width="24" height="5" rx="2.5" fill="#D6DCF6"/>
  <circle cx="70" cy="66" r="14" fill="#12B886" fill-opacity=".14"/>
  <path d="M64 66l4 4 8-8" stroke="#12B886" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>
</svg>`
</script>

<style scoped>
.empty {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 6px; padding: 40px 24px; text-align: center; color: var(--c-muted);
}
.empty__art { margin-bottom: 6px; }
.empty__title { font-size: 15px; color: var(--c-text); }
.empty__desc { margin: 0 0 12px; max-width: 420px; font-size: 13px; line-height: 1.7; }
</style>
