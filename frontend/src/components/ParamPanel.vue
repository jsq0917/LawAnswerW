<template>
  <div class="params">
    <div class="params__head row row--between">
      <strong>生成参数</strong>
      <button class="params__reset" type="button" @click="reset">恢复默认</button>
    </div>

    <label class="params__row">
      <span class="params__label">模型</span>
      <el-select :model-value="model" size="small" style="width: 100%" @update:model-value="onModel">
        <el-option label="deepseek-v4.1-flash（默认）" value="deepseek-v4.1-flash"/>
        <el-option label="qwen-plus" value="qwen-plus"/>
        <el-option label="qwen-turbo" value="qwen-turbo"/>
      </el-select>
    </label>

    <label class="params__row">
      <span class="params__label">温度 <b class="mono">{{ temperature.toFixed(2) }}</b></span>
      <el-slider
        :model-value="temperature" :min="0" :max="1.5" :step="0.05" size="small"
        :show-tooltip="false" @update:model-value="onNum('temperature', $event)"
      />
    </label>

    <label class="params__row">
      <span class="params__label">Top P <b class="mono">{{ topP.toFixed(2) }}</b></span>
      <el-slider
        :model-value="topP" :min="0.1" :max="1" :step="0.05" size="small"
        :show-tooltip="false" @update:model-value="onNum('top_p', $event)"
      />
    </label>

    <label class="params__row">
      <span class="params__label">最长输出 token</span>
      <el-input-number
        :model-value="maxTokens" :min="256" :max="8192" :step="256" size="small"
        style="width: 100%" @update:model-value="onNum('max_tokens', $event)"
      />
    </label>

    <label class="params__row">
      <span class="params__label">历史轮数 <span class="faint">（0–20）</span></span>
      <el-input-number
        :model-value="historyRounds" :min="0" :max="20" :step="1" size="small"
        style="width: 100%" @update:model-value="onNum('history_rounds', $event)"
      />
    </label>

    <p class="params__hint faint">参数与会话绑定，切换会话自动回填。</p>
  </div>
</template>

<script setup lang="ts">
/** 参数面板（§4.2 P3）：与会话绑定，改动即时 PATCH 回后端。 */
import type {SessionParams} from '@/api/sessions'

const props = defineProps<{
  model: string
  temperature: number
  topP: number
  maxTokens: number
  historyRounds: number
}>()

const emit = defineEmits<{(e: 'change', patch: SessionParams): void}>()

const DEFAULTS: SessionParams = {
  model: 'deepseek-v4.1-flash',
  temperature: 0.3,
  top_p: 0.8,
  max_tokens: 2048,
  history_rounds: 5,
}

function onModel(value: string | number | boolean | Record<string, unknown> | undefined) {
  emit('change', {model: String(value)})
}

function onNum(key: keyof SessionParams, value: number | undefined) {
  if (typeof value !== 'number' || Number.isNaN(value)) return
  emit('change', {[key]: value} as SessionParams)
}

function reset() {
  emit('change', {...DEFAULTS})
}

// props 由父组件透传，这里不直接改，避免"两处状态源"
void props
</script>

<style scoped>
.params { display: flex; flex-direction: column; gap: 14px; }
.params__head { font-size: 13px; }
.params__reset {
  border: none; background: transparent; color: var(--c-primary);
  font-size: 12px; cursor: pointer; padding: 0;
}
.params__row { display: flex; flex-direction: column; gap: 2px; }
.params__label { display: flex; justify-content: space-between; font-size: 12.5px; color: var(--c-muted); }
.params__hint { margin: 0; font-size: 11.5px; line-height: 1.6; }
</style>
