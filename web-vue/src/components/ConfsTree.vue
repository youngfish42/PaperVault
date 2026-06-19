<!--
 * @Author: 0x3E5
 * @Date: 2023-02-12 17:29:45
 * @LastEditTime: 2023-03-06 10:34:09
 * @LastEditors: 0x3E5
 * @Description: 
 * @FilePath: \web-vue\src\components\ConfsTree.vue
-->
<script setup lang="ts">
import { computed, ref, shallowRef, watch } from 'vue'
import { useI18n } from '@/utils/i18n'

const { t, lang } = useI18n()

interface TreeMeta {
  total: number
  fetched: number
  truncated: boolean
}

const props = withDefaults(
  defineProps<{
    data: Record<string, Record<string, unknown[]>> | unknown
    meta?: TreeMeta
  }>(),
  {
    meta: () => ({ total: 0, fetched: 0, truncated: false })
  }
)
const emits = defineEmits<{
  (e: 'click', val: { level: number; key?: string; parent?: string }): void
}>()

interface Tree {
  label: string
  children?: Tree[]
}

const fetchedCount = ref(0)
const tree = shallowRef<Tree[]>([
  { label: `${t('tree.all')} (0)`, children: [] }
])

const defaultProps = {
  children: 'children',
  label: 'label'
}

const rootLabel = computed(() => {
  // Prefer the authoritative server-side total when available so the sidebar
  // does not under-count after the result list is truncated client-side.
  const n = props.meta?.total ?? fetchedCount.value
  const suffix = props.meta?.truncated
    ? ` ${t('tree.truncatedMark').replace('{n}', String(fetchedCount.value))}`
    : ''
  return `${t('tree.all')} (${n})${suffix}`
})

const formatData = (treeData: any, target: Tree): number => {
  if (Array.isArray(treeData)) {
    fetchedCount.value += treeData.length
    target.label = `${target.label} (${treeData.length})`
    return treeData.length
  }
  let level2Total = 0
  for (const k in treeData) {
    const itm: Tree = { label: k, children: [] }
    target.children?.push(itm)
    level2Total += Number(formatData(treeData[k], itm)) || 0
  }
  if (!target.label.startsWith(t('tree.all'))) {
    target.label = `${target.label} (${level2Total})`
  }
  return level2Total
}

const rebuild = (v: unknown): void => {
  fetchedCount.value = 0
  const root: Tree = { label: rootLabel.value, children: [] }
  formatData(v, root)
  root.label = rootLabel.value
  tree.value = [root]
}

const treeNodeClick = (_objData: Tree, node: any) => {
  if (node.level === 3) {
    emits('click', {
      level: node.level,
      key: node.data.label.split(' ')[0],
      parent: node.parent.data.label.split(' ')[0]
    })
  } else {
    emits('click', { level: node.level, key: node.data.label.split(' ')[0] })
  }
}

watch(
  () => props.data,
  v => rebuild(v),
  {
    immediate: true,
    deep: true
  }
)

watch(
  () => props.meta,
  () => rebuild(props.data),
  { deep: true }
)
watch(lang, () => rebuild(props.data))
</script>
<template>
  <el-card class="tree-card pv-compact-card" shadow="never">
    <el-scrollbar height="320px">
      <el-tree
        :data="tree"
        :props="defaultProps"
        :highlight-current="true"
        :expand-on-click-node="false"
        :default-expand-all="true"
        @node-click="treeNodeClick"
      />
    </el-scrollbar>
    <div v-if="props.meta?.truncated" class="tree-truncated-hint">
      {{ t('tree.truncatedHint').replace('{n}', String(fetchedCount)) }}
    </div>
  </el-card>
</template>

<style scoped>
.tree-card {
  user-select: none;
}
.tree-card :deep(.el-tree-node__label) {
  font-size: 13px;
}
.tree-truncated-hint {
  margin-top: 8px;
  padding: 6px 8px;
  font-size: 12px;
  line-height: 1.4;
  color: var(--el-color-warning, #e6a23c);
  background: var(--el-color-warning-light-9, #fdf6ec);
  border-radius: 4px;
}
</style>
