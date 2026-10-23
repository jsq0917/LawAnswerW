<template>
  <div class="editor">
    <div class="editor__head row row--between">
      <div class="row" style="gap: 8px">
        <span class="pill" :class="chunk.chunk_type === 'parent' ? 'pill--info' : ''">
          {{ chunk.chunk_type === 'parent' ? '父块' : '子块' }}
        </span>
        <span v-if="chunk.article_no" class="mono editor__article">{{ chunk.article_no }}</span>
        <span class="faint">{{ chunk.char_count }} 字</span>
      </div>
      <div class="row" style="gap: 8px">
        <span class="pill" :class="chunk.in_milvus ? 'pill--ok' : 'pill--warn'">
          {{ chunk.in_milvus ? '已入向量库' : '未入向量库' }}
        </span>
        <button v-if="!editing" class="btn btn--ghost btn--sm" type="button" @click="startEdit">编辑</button>
        <template v-else>
          <button class="btn btn--ghost btn--sm" type="button" :disabled="saving" @click="cancel">取消</button>
          <button class="btn btn--sm" type="button" :disabled="saving || !dirty" @click="save">
            {{ saving ? '保存中…' : '保存并重新向量化' }}
          </button>
        </template>
        <button class="btn btn--ghost btn--sm" type="button" @click="$emit('remove', chunk)">删除</button>
      </div>
    </div>

    <p v-if="editing" class="editor__warn">
      修改将重新生成向量（旧向量先删后写），检索结果会立即更新。
    </p>

    <div v-if="!editing" class="editor__content">{{ chunk.content }}</div>
    <textarea
      v-else
      v-model="draft"
      class="input editor__textarea"
      rows="16"
      spellcheck="false"
    />

    <dl class="editor__meta">
      <div><dt>章节路径</dt><dd>{{ chunk.chapter_path || '—' }}</dd></div>
      <div><dt>块序号</dt><dd class="mono">{{ chunk.chunk_index }}</dd></div>
      <div><dt>父块 ID</dt><dd class="mono ellipsis">{{ chunk.parent_id || '—' }}</dd></div>
      <div><dt>块 ID</dt><dd class="mono ellipsis">{{ chunk.id }}</dd></div>
    </dl>
  </div>
</template>

<script setup lang="ts">
/** 块详情 + 编辑（§4.2 P5）：保存后后端重新向量化，父组件负责刷新列表。 */
import {computed, ref} from 'vue'

import type {ChunkItem} from '@/api/kb'

const props = defineProps<{chunk: ChunkItem; saving?: boolean}>()
const emit = defineEmits<{(e: 'save', chunk: ChunkItem, content: string): void; (e: 'remove', chunk: ChunkItem): void}>()

const editing = ref(false)
const draft = ref('')

const dirty = computed(() => draft.value.trim() !== props.chunk.content.trim())

function startEdit() {
  draft.value = props.chunk.content
  editing.value = true
}

function cancel() {
  editing.value = false
  draft.value = ''
}

function save() {
  emit('save', props.chunk, draft.value.trim())
  editing.value = false
}
</script>

<style scoped>
.editor { display: flex; flex-direction: column; gap: 12px; }
.editor__head { flex-wrap: wrap; gap: 8px; }
.editor__article { font-size: 13px; color: var(--c-primary); font-weight: 600; }
.editor__warn {
  margin: 0; padding: 8px 12px; border-radius: 9px;
  background: var(--c-warn-soft); color: #8A5600; font-size: 12.5px;
}
.editor__content {
  white-space: pre-wrap; word-break: break-word;
  font-size: 14px; line-height: 1.85;
  padding: 14px 16px; border-radius: 12px;
  background: var(--c-bg); border: 1px solid var(--c-border);
  max-height: 52vh; overflow: auto;
}
.editor__textarea { font-family: var(--font-mono); font-size: 13px; line-height: 1.8; }
.editor__meta {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 8px 16px; margin: 0; padding-top: 10px; border-top: 1px dashed var(--c-border);
  font-size: 12.5px;
}
.editor__meta dt { color: var(--c-faint); }
.editor__meta dd { margin: 2px 0 0; color: var(--c-text); }
</style>
