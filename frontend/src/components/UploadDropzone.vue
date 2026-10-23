<template>
  <div
    class="dropzone"
    :class="{'dropzone--over': over, 'dropzone--disabled': disabled}"
    @dragover.prevent="over = true"
    @dragleave.prevent="over = false"
    @drop.prevent="onDrop"
    @click="pick"
  >
    <input ref="input" type="file" multiple class="sr-only" :accept="accept" @change="onPick"/>
    <div class="dropzone__icon">⇪</div>
    <p class="dropzone__title">拖拽文件到这里，或<button type="button" class="dropzone__link" @click.stop="pick">点击选择</button></p>
    <p class="dropzone__hint faint">支持 PDF / PPTX / Markdown / 图片（PNG、JPG），单文件 ≤ 20 MB</p>

    <ul v-if="modelValue.length" class="dropzone__list">
      <li v-for="(f, i) in modelValue" :key="`${f.name}-${i}`" class="dropzone__file">
        <span class="ellipsis">{{ f.name }}</span>
        <span class="faint">{{ sizeText(f.size) }}</span>
        <button type="button" class="dropzone__remove" @click.stop="remove(i)">×</button>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
/** 上传区（§4.2 P4）：拖拽/点击、多文件、可删除。 */
import {ref} from 'vue'

const props = defineProps<{modelValue: File[]; accept?: string; disabled?: boolean}>()
const emit = defineEmits<{(e: 'update:modelValue', files: File[]): void}>()

const input = ref<HTMLInputElement | null>(null)
const over = ref(false)
const accept = props.accept || '.pdf,.pptx,.md,.markdown,.png,.jpg,.jpeg'

function pick() {
  if (props.disabled) return
  input.value?.click()
}

function onPick(e: Event) {
  const el = e.target as HTMLInputElement
  push(Array.from(el.files || []))
  el.value = ''
}

function onDrop(e: DragEvent) {
  over.value = false
  if (props.disabled) return
  push(Array.from(e.dataTransfer?.files || []))
}

function push(incoming: File[]) {
  const merged = [...props.modelValue]
  for (const f of incoming) {
    if (!merged.some((m) => m.name === f.name && m.size === f.size)) merged.push(f)
  }
  emit('update:modelValue', merged)
}

function remove(index: number) {
  const next = [...props.modelValue]
  next.splice(index, 1)
  emit('update:modelValue', next)
}

function sizeText(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}
</script>

<style scoped>
.dropzone {
  border: 1.5px dashed var(--c-border-strong);
  border-radius: var(--r-card);
  background: #fff;
  padding: 22px;
  text-align: center;
  cursor: pointer;
  transition: border-color var(--dur) var(--ease), background var(--dur) var(--ease);
}
.dropzone:hover, .dropzone--over { border-color: var(--c-primary); background: var(--c-primary-soft); }
.dropzone--disabled { opacity: .6; cursor: not-allowed; }
.dropzone__icon { font-size: 22px; color: var(--c-primary); }
.dropzone__title { margin: 6px 0 4px; font-size: 14px; }
.dropzone__link { border: none; background: transparent; color: var(--c-primary); cursor: pointer; padding: 0; font-size: 14px; }
.dropzone__hint { margin: 0; font-size: 12px; }
.dropzone__list { list-style: none; margin: 14px 0 0; padding: 0; display: flex; flex-direction: column; gap: 6px; text-align: left; }
.dropzone__file {
  display: flex; align-items: center; gap: 10px;
  padding: 7px 10px; border-radius: 9px; background: var(--c-bg);
  font-size: 13px;
}
.dropzone__remove {
  margin-left: auto; width: 22px; height: 22px; border: none; border-radius: 6px;
  background: transparent; color: var(--c-muted); cursor: pointer; font-size: 15px; line-height: 1;
}
.dropzone__remove:hover { background: var(--c-danger-soft); color: var(--c-danger); }
</style>
