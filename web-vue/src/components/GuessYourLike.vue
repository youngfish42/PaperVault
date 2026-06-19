<script setup lang="ts">
import { useI18n } from '@/utils/i18n'

const { t } = useI18n()
const props = defineProps(['loading', 'result'])
const emits = defineEmits(['search-guess'])
const search = (searchContent: string): void => {
  emits('search-guess', searchContent)
}
</script>

<template>
  <el-card class="pv-guess-card pv-compact-card" shadow="never">
    <template #header>
      <div class="card-header">
        <span class="title">{{ t('guess.header') }}</span>
      </div>
    </template>
    <div class="guess-like-list" v-loading="props.loading">
      <ol v-show="props.result.length > 0">
        <li v-for="(itm, index) in props.result" :key="index">
          <el-link :underline="false" @click="search(itm)">{{ itm }}</el-link>
        </li>
      </ol>
      <el-empty
        v-show="props.result.length <= 0"
        :description="t('guess.empty')"
        :image-size="40"
      />
    </div>
  </el-card>
</template>

<style scoped>
.pv-guess-card :deep(.el-card__header) {
  padding: 10px 14px;
}
.title {
  font-weight: 600;
  font-size: 14px;
}
.guess-like-list ol {
  padding-left: 18px;
  margin: 0;
  line-height: 1.8;
  font-size: 13px;
}
.guess-like-list li {
  margin-bottom: 2px;
}
</style>
