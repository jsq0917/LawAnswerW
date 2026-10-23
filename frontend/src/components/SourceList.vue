<template>
  <div class="sources">
    <button class="sources__toggle" type="button" @click="open = !open">
      <span class="sources__caret" :class="{'sources__caret--open': open}">▸</span>
      来源 {{ sources.length }} 条
      <span class="faint">（点击查看法条原文）</span>
    </button>

    <ul v-if="open" class="sources__list stagger">
      <li v-for="(s, i) in sources" :key="i" class="sources__item">
        <div class="row row--between">
          <span class="sources__title">
            <span class="sources__law">《{{ s.law }}》</span>
            <span v-if="s.chapter_path" class="faint">{{ s.chapter_path }} · </span>
            <span class="mono">{{ s.article_no }}</span>
          </span>
          <span class="row" style="gap: 6px">
            <span v-if="s.kind === 'qa'" class="pill pill--ok">问答库</span>
            <span class="pill">相关度 {{ scoreText(s.score) }}</span>
          </span>
        </div>
        <p class="sources__snippet">{{ s.snippet }}</p>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import {ref} from 'vue'

import type {SourceRef} from '@/api/sessions'

defineProps<{sources: SourceRef[]}>()
const open = ref(false)

function scoreText(score: number): string {
  // 重排分是无界 logit，问答相似度是 0–1；统一按两位小数展示，不假装是百分比
  return Number.isFinite(score) ? score.toFixed(2) : '-'
}
</script>

<style scoped>
.sources { margin-top: 10px; border-top: 1px dashed var(--c-border); padding-top: 8px; }
.sources__toggle {
  display: inline-flex; align-items: center; gap: 6px;
  border: none; background: transparent; padding: 2px 0;
  color: var(--c-muted); font-size: 12.5px; cursor: pointer;
}
.sources__toggle:hover { color: var(--c-primary); }
.sources__caret { display: inline-block; transition: transform var(--dur) var(--ease); }
.sources__caret--open { transform: rotate(90deg); }
.sources__list { list-style: none; margin: 8px 0 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.sources__item {
  padding: 8px 10px; border-radius: 10px;
  background: var(--c-bg); border: 1px solid var(--c-border);
}
.sources__title { font-size: 12.5px; }
.sources__law { color: var(--c-primary); font-weight: 600; }
.sources__snippet {
  margin: 6px 0 0; font-size: 12.5px; color: var(--c-muted);
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;
}
</style>
