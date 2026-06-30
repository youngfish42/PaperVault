<script setup lang="ts">
/**
 * Reusable "AI suggested keywords" panel.
 *
 * Used in two places:
 *   1. The right sidebar of the results view, fed by the fire-and-forget
 *      post-search suggestion call.
 *   2. Inside the hero "AI search" dialog, fed by an explicit
 *      user-initiated call.
 *
 * The component deliberately knows nothing about the parent query string
 * or how the picked keywords are merged — it only owns the visual
 * list + multi-select state + two emit buttons. Parents receive the raw
 * picked strings (or a single click for legacy replace) and decide how
 * to apply them. This split keeps the same look-and-feel across both
 * call sites without coupling either to the other's data shape.
 */
import { ref, watch } from 'vue'

const props = defineProps<{
  keywords: string[]
  loading: boolean
  title: string
  providerLabel?: string
  emptyText: string
  singleReplaceText: string
  mergeButtonText: string
}>()

const emit = defineEmits<{
  (e: 'pick-many', picked: string[]): void
  (e: 'replace', keyword: string): void
}>()

// Local checkbox state. Reset whenever the upstream keyword list changes
// so an old pick never carries over into a freshly returned result set.
const picked = ref<string[]>([])
watch(
  () => props.keywords,
  () => {
    picked.value = []
  }
)

const onPickMany = (): void => {
  if (!picked.value.length) return
  emit('pick-many', [...picked.value])
}

const onReplaceFirst = (): void => {
  const first = props.keywords[0]
  if (!first) return
  emit('replace', first)
}
</script>

<template>
  <el-card shadow="never" class="pv-ai-suggest-card">
    <template v-if="title || providerLabel" #header>
      <div class="pv-ai-suggest-header">
        <span v-if="title" class="pv-ai-suggest-title">{{ title }}</span>
        <span v-if="providerLabel" class="pv-ai-suggest-provider">
          {{ providerLabel }}
        </span>
      </div>
    </template>

    <div v-loading="loading" class="pv-ai-suggest-body">
      <el-checkbox-group v-model="picked" class="pv-ai-suggest-list">
        <el-checkbox
          v-for="kw in keywords"
          :key="kw"
          :value="kw"
          class="pv-ai-suggest-item"
        >
          <span class="pv-ai-suggest-item-label">{{ kw }}</span>
        </el-checkbox>
      </el-checkbox-group>
      <el-empty
        v-if="!loading && keywords.length === 0"
        :description="emptyText"
        :image-size="60"
      />
    </div>

    <div v-if="keywords.length || picked.length" class="pv-ai-suggest-actions">
      <el-button :disabled="!keywords.length" @click="onReplaceFirst">
        {{ singleReplaceText }}
      </el-button>
      <el-button type="primary" :disabled="!picked.length" @click="onPickMany">
        {{ mergeButtonText }} ({{ picked.length }})
      </el-button>
    </div>
  </el-card>
</template>

<style scoped>
.pv-ai-suggest-card :deep(.el-card__header) {
  padding: 10px 14px;
}
.pv-ai-suggest-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.pv-ai-suggest-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary, #303133);
}
.pv-ai-suggest-provider {
  font-size: 11px;
  color: var(--el-text-color-secondary, #909399);
  font-variant-numeric: tabular-nums;
}
.pv-ai-suggest-body {
  padding: 8px 4px 4px;
  min-height: 60px;
}
.pv-ai-suggest-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.pv-ai-suggest-item {
  margin-right: 0;
  width: 100%;
  word-break: break-word;
  line-height: 1.5;
}
.pv-ai-suggest-item-label {
  font-size: 13px;
  color: var(--el-text-color-regular, #4c4d4f);
}
.pv-ai-suggest-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
  padding: 0 4px 4px;
}
</style>
