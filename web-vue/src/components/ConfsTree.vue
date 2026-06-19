<!--
 * @Author: 0x3E5
 * @Date: 2023-02-12 17:29:45
 * @LastEditTime: 2023-03-06 10:34:09
 * @LastEditors: 0x3E5
 * @Description: 
 * @FilePath: \web-vue\src\components\ConfsTree.vue
-->
<script setup lang="ts">
import { ref, shallowRef, watch } from 'vue'

const props = defineProps<{ data: Record<string, Record<string, unknown[]>> | unknown }>()
const emits = defineEmits<{
  (e: 'click', val: { level: number; key?: string; parent?: string }): void
}>()

interface Tree {
  label: string
  children?: Tree[]
}

const total = ref(0)
const tree = shallowRef<Tree[]>([
  { label: 'All (0)', children: [] }
])

const defaultProps = {
  children: 'children',
  label: 'label'
}

const formatData = (treeData: any, target: Tree): number => {
  if (Array.isArray(treeData)) {
    total.value += treeData.length
    tree.value[0].label = `All (${total.value})`
    target.label = `${target.label} (${treeData.length})`
    return treeData.length
  }
  let level2Total = 0
  for (const k in treeData) {
    const itm: Tree = { label: k, children: [] }
    target.children?.push(itm)
    level2Total += Number(formatData(treeData[k], itm)) || 0
  }
  if (!target.label.startsWith('All')) {
    target.label = `${target.label} (${level2Total})`
  }
  return level2Total
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
  v => {
    total.value = 0
    const root: Tree = { label: 'All (0)', children: [] }
    formatData(v, root)
    tree.value = [root]
  },
  {
    immediate: true,
    deep: true
  }
)
</script>
<template>
  <el-card class="tree-card mb-15" shadow="never">
    <el-scrollbar height="260px">
      <el-tree
        :data="tree"
        :props="defaultProps"
        :highlight-current="true"
        :expand-on-click-node="false"
        :default-expand-all="true"
        @node-click="treeNodeClick"
      />
    </el-scrollbar>
  </el-card>
</template>

<style scoped>
.tree-card {
  user-select: none;
  height: 300px;
}
</style>
