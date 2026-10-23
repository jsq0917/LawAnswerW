<template>
  <div class="md" v-html="html"/>
</template>

<script setup lang="ts">
/** Markdown 渲染 + 法条引用高亮 + 流式光标。
 *
 * 引用正则匹配"（《法律名》条号）"，渲染成等宽胶囊（§4.1 等宽感行内标记）；
 * 输出前统一过 DOMPurify，避免模型输出里的 HTML/脚本注入。
 */
import {computed} from 'vue'
import {marked} from 'marked'
import DOMPurify from 'dompurify'

const props = defineProps<{content: string; streaming?: boolean}>()

marked.setOptions({gfm: true, breaks: true})

const CITATION = /（《([^》\n]{2,40})》([^）\n]{0,40})）/g

const html = computed(() => {
  const rendered = marked.parse(props.content || '', {async: false}) as string
  const cited = rendered.replace(
    CITATION,
    (_m, law: string, article: string) =>
      `<span class="citation">《${law}》${article}</span>`,
  )
  const safe = DOMPurify.sanitize(cited, {ADD_ATTR: ['target']})
  return props.streaming ? `${safe}<span class="stream-cursor"></span>` : safe
})
</script>
