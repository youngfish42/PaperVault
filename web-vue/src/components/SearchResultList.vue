<script setup lang="ts">
import { ref, reactive, computed } from 'vue'
import FILE from '@/utils/file'
import type { PaperItem } from '@/api/paper'
import { useI18n } from '@/utils/i18n'

const { t } = useI18n()

const emits = defineEmits<{
  (e: 'searchAuthor', val: string): void
}>()

const resultList = ref<PaperItem[]>([])

const sortMethod = ref<'Year' | 'Conf'>('Year')

const exportFile = (method: string): void => {
  if (method === 'csv') {
    FILE.exportCSV(resultList.value, 'result.csv')
  } else if (method === 'txt') {
    FILE.exportTxt(resultList.value, 'result.txt')
  }
}

const deleteResult = (item: PaperItem): void => {
  const idx = resultList.value.indexOf(item)
  if (idx >= 0) {
    resultList.value.splice(idx, 1)
  }
}

const jumpUrl = (url?: string | null) => {
  if (url) window.open(url)
}

const searchAuthor = (author: string): void => {
  emits('searchAuthor', author)
}

const filterResult: (target: any, option: any) => void = (target, option) => {
  let next: PaperItem[] = []
  const { level, key, parent } = option
  if (Number(level) === 1) {
    next = collectAll(target)
  } else if (Number(level) === 2) {
    const group = target?.[key] ?? {}
    for (const k in group) next = next.concat(group[k])
  } else if (Number(level) === 3) {
    next = next.concat(target?.[parent]?.[key] ?? [])
  }
  resultList.value = next
  changeSortMethod(sortMethod.value)
}

const collectAll = (target: any): PaperItem[] => {
  const out: PaperItem[] = []
  const walk = (node: any) => {
    if (Array.isArray(node)) {
      out.push(...node)
    } else if (node && typeof node === 'object') {
      for (const k in node) walk(node[k])
    }
  }
  walk(target)
  return out
}

const changeSortMethod = (method: any): void => {
  if (method === 'Year') {
    resultList.value = resultList.value
      .slice()
      .sort((a, b) => Number(b.year) - Number(a.year))
  } else if (method === 'Conf') {
    resultList.value = resultList.value.slice().sort((a, b) => {
      const a1 = (a.conf || '').toUpperCase()
      const b1 = (b.conf || '').toUpperCase()
      if (a1 < b1) return -1
      if (a1 > b1) return 1
      return 0
    })
  }
}

type PAGE = {
  current: number
  size: number
}
const page: PAGE = reactive({
  current: 1,
  size: 50
})

const pageCurrentChange = (v: number): void => {
  page.current = v
}

const pageSizeChange = (v: number): void => {
  page.current = 1
  page.size = v
}

const virtualList = computed(() => {
  return resultList.value.slice(
    (page.current - 1) * page.size,
    page.current * page.size
  )
})

const formatAuthors = (authors?: string[] | null): string => {
  if (!authors || authors.length === 0) return ''
  return authors.join(' · ')
}

defineExpose({
  filterResult
})
</script>

<template>
  <el-card class="pv-result-card pv-flat-card" shadow="never">
    <div class="pv-result-toolbar" v-show="resultList.length > 0">
      <div class="left">
        <span class="muted">{{ t('result.sortBy') }}</span>
        <el-radio-group
          v-model="sortMethod"
          size="small"
          @change="changeSortMethod"
        >
          <el-radio-button :value="'Year'">
            {{ t('result.sort.year') }}
          </el-radio-button>
          <el-radio-button :value="'Conf'">
            {{ t('result.sort.conf') }}
          </el-radio-button>
        </el-radio-group>
        <span class="muted total">{{ resultList.length }}</span>
      </div>
      <div class="right">
        <el-link @click="exportFile('txt')" :underline="false">
          <el-icon class="el-icon--left"><Document /></el-icon>
          {{ t('result.export.txt') }}
        </el-link>
        <el-link @click="exportFile('csv')" :underline="false">
          <el-icon class="el-icon--left"><Collection /></el-icon>
          {{ t('result.export.csv') }}
        </el-link>
      </div>
    </div>

    <ul class="pv-paper-list" v-show="resultList.length > 0">
      <li v-for="(itm, index) in virtualList" :key="index" class="pv-paper-itm">
        <div class="row-1">
          <el-tag size="small" type="warning" class="tag-conf">
            {{ itm.conf }}
          </el-tag>
          <el-tag size="small" type="danger" class="tag-year">
            {{ itm.year }}
          </el-tag>
          <el-link
            class="paper-title"
            :href="itm.url ?? undefined"
            :underline="false"
            target="_blank"
          >
            {{ itm.title }}
          </el-link>
          <span class="row-actions">
            <el-popover
              v-if="itm.abstract"
              placement="left-start"
              :width="420"
              trigger="click"
            >
              <template #reference>
                <el-tag
                  size="small"
                  class="pointer no-select"
                  type="success"
                  effect="plain"
                >
                  {{ t('result.abstract') }}
                </el-tag>
              </template>
              <div>
                <h3 class="mb-10">{{ itm.title }}</h3>
                <p class="abstract-text">{{ itm.abstract }}</p>
              </div>
            </el-popover>
            <el-tag
              v-if="itm.code && itm.code !== '#'"
              size="small"
              class="pointer no-select"
              effect="plain"
              @click="jumpUrl(itm.code)"
            >
              {{ t('result.code') }}
            </el-tag>
            <el-icon
              class="delete pointer no-select"
              @click="deleteResult(itm)"
            >
              <CloseBold />
            </el-icon>
          </span>
        </div>
        <div class="row-2 text-ellipsis" :title="formatAuthors(itm.authors)">
          <template
            v-for="(author, authorIndex) in itm.authors"
            :key="authorIndex"
          >
            <el-link
              class="author"
              :underline="false"
              @click="searchAuthor(author)"
            >
              {{ author }}
            </el-link>
            <span
              v-if="authorIndex < (itm.authors?.length ?? 0) - 1"
              class="author-sep"
            >
              ·
            </span>
          </template>
        </div>
      </li>
    </ul>

    <el-empty
      v-show="resultList.length <= 0"
      :description="t('result.empty')"
    />

    <div class="mt-15" v-show="resultList.length > 0">
      <el-pagination
        class="align-right"
        v-model:current-page="page.current"
        v-model:page-size="page.size"
        :page-sizes="[20, 50, 100, 150, 200]"
        layout="sizes, prev, pager, next, jumper, total"
        :total="resultList.length"
        @size-change="pageSizeChange"
        @current-change="pageCurrentChange"
      />
    </div>
  </el-card>
</template>

<style scoped>
.pv-result-card {
  background: transparent;
}
.pv-result-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px;
  margin-bottom: 8px;
  background: var(--el-fill-color-light, #f5f7fa);
  border-radius: 6px;
}
.pv-result-toolbar .left,
.pv-result-toolbar .right {
  display: flex;
  align-items: center;
  gap: 12px;
}
.muted {
  color: var(--el-text-color-secondary, #909399);
  font-size: 13px;
}
.total {
  font-weight: 600;
  color: var(--el-color-primary, #409eff);
}

.pv-paper-list {
  list-style: none;
  padding: 0;
  margin: 0;
}
.pv-paper-itm {
  padding: 8px 10px;
  border-bottom: 1px solid var(--el-border-color-lighter, #ebeef5);
  transition: background-color 0.15s;
}
.pv-paper-itm:hover {
  background: var(--el-fill-color-lighter, #fafafa);
}
.row-1 {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.tag-conf,
.tag-year {
  flex-shrink: 0;
}
.paper-title {
  font-size: 15px;
  font-weight: 500;
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.paper-title :deep(.el-link__inner) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: block;
}
.row-actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}
.delete {
  color: var(--el-text-color-placeholder, #c0c4cc);
  font-size: 14px;
  margin-left: 4px;
}
.delete:hover {
  color: var(--el-color-danger, #f56c6c);
}
.row-2 {
  margin-top: 2px;
  padding-left: 2px;
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
}
.author {
  font-size: 12px;
  color: var(--el-text-color-secondary, #909399);
}
.author:hover {
  color: var(--el-color-primary, #409eff);
}
.author-sep {
  margin: 0 4px;
  color: var(--el-border-color, #dcdfe6);
}
.abstract-text {
  max-height: 60vh;
  overflow-y: auto;
  line-height: 1.6;
  white-space: pre-wrap;
}
</style>
