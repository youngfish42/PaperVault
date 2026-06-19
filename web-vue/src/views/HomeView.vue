<script setup lang="ts">
import { onMounted, reactive, ref, shallowRef, computed } from 'vue'
import { useDark, useToggle } from '@vueuse/core'
import { ElMessage, ElLoading } from 'element-plus'
import AdvancedSettingDlg from '@/components/AdvancedSettingDlg.vue'
import ConfsTree from '@/components/ConfsTree.vue'
import SearchResultList from '@/components/SearchResultList.vue'
import GuessYourLike from '@/components/GuessYourLike.vue'
import {
  listConfs,
  searchPapers,
  suggestKeywords,
  type PaperItem
} from '@/api/paper'
import { useI18n } from '@/utils/i18n'

const { t, toggle: toggleLang } = useI18n()

const firstEntry = ref(true)
const availableConfs = shallowRef<string[]>([])

const searchContent = reactive({
  query: '',
  searchtype: 'title' as 'title' | 'author',
  year: '',
  sp_year: '',
  sp_author: '',
  confs: [] as string[]
})

const SEARCH_TYPE_LIST = computed(() => [
  { label: t('search.type.title'), value: 'title' },
  { label: t('search.type.author'), value: 'author' }
])

const queryResult = shallowRef<Record<string, Record<string, PaperItem[]>>>({})

const settingDlg = ref<InstanceType<typeof AdvancedSettingDlg> | null>(null)
const searchResult = ref<InstanceType<typeof SearchResultList> | null>(null)

const guessLoading = ref(false)
const guessList = ref<string[]>([])

const groupByConfYear = (items: PaperItem[]) => {
  const out: Record<string, Record<string, PaperItem[]>> = {}
  for (const it of items) {
    const conf = it.conf || 'UNKNOWN'
    const year = String(it.year || 'NA')
    if (!out[conf]) out[conf] = {}
    if (!out[conf][year]) out[conf][year] = []
    out[conf][year].push(it)
  }
  return out
}

const buildQuery = () => {
  const params: Record<string, unknown> = {
    field: searchContent.searchtype,
    sort: '-year',
    page: 1,
    size: 200
  }
  if (searchContent.query) params.q = searchContent.query
  if (searchContent.sp_author) params.author = searchContent.sp_author
  if (searchContent.sp_year) {
    const y = Number(searchContent.sp_year)
    if (!Number.isNaN(y)) {
      params.since = y
      params.until = y
    }
  } else if (searchContent.year) {
    const y = Number(searchContent.year)
    if (!Number.isNaN(y)) params.since = y
  }
  if (searchContent.confs.length > 0) {
    params.conf = [...searchContent.confs]
  }
  return params
}

const search = (): void => {
  if (searchContent.query === '' && searchContent.sp_author === '') {
    ElMessage.warning(t('search.warn.empty'))
    return
  }
  const loading = ElLoading.service({
    lock: true,
    text: t('search.button') + '...'
  })
  queryResult.value = {}
  guessList.value = []

  searchPapers(buildQuery())
    .then(res => {
      queryResult.value = groupByConfYear(res.items || [])
      handleTreeClick({ level: 1 })
    })
    .catch(err => {
      console.error(err)
    })
    .finally(() => {
      firstEntry.value = false
      loading && loading.close()
    })

  if (searchContent.query) {
    guessLoading.value = true
    suggestKeywords(searchContent.query)
      .then(res => {
        guessList.value = res.keywords || []
      })
      .catch(err => {
        console.error(err)
      })
      .finally(() => {
        guessLoading.value = false
      })
  }
}

const handleSearchAuthor = (data: string): void => {
  searchContent.query = ''
  searchContent.sp_author = data
  search()
}

const handleSearchGuess = (data: string): void => {
  searchContent.query = data
  search()
}

const handleUpdateForm = (data: {
  query: string
  searchtype: string
  year: string
  sp_year: string
  sp_author: string
  confs: string[]
}): void => {
  Object.assign(searchContent, data, {
    searchtype: data.searchtype as 'title' | 'author'
  })
}

const handleTreeClick = (data: {
  level: number
  key?: string
  parent?: string
}): void => {
  if (searchResult.value) {
    ;(searchResult.value as any).filterResult(queryResult.value, data)
  }
}

const showSetting = (): void => {
  if (settingDlg.value) {
    ;(settingDlg.value as any).isVisible = true
  }
}

const isDark = useDark()
const toggleDark = useToggle(isDark)

onMounted(async () => {
  try {
    const res = await listConfs()
    const names = (res.items || []).map(c => c.name)
    availableConfs.value = names
    if (searchContent.confs.length === 0) {
      searchContent.confs = [...names]
    }
  } catch (err) {
    console.error('Failed to load confs', err)
  }
})
</script>

<template>
  <main class="full pos-relative">
    <!-- 首屏：搜索框居中 -->
    <section v-if="firstEntry" class="pv-hero">
      <div class="pv-container pv-hero-inner">
        <h1 class="title mb-15">
          <a href="/">{{ t('app.title') }}</a>
        </h1>
        <el-input
          v-model="searchContent.query"
          :placeholder="t('search.placeholder')"
          clearable
          @keyup.enter="search"
          size="large"
          class="mb-10"
        >
          <template #prepend>
            <el-select
              v-model="searchContent.searchtype"
              style="width: 110px"
              size="large"
            >
              <el-option
                v-for="(itm, index) in SEARCH_TYPE_LIST"
                :key="index"
                :label="itm.label"
                :value="itm.value"
              />
            </el-select>
          </template>
          <template #append>
            <el-button icon="Search" @click="search">
              {{ t('search.button') }}
            </el-button>
          </template>
        </el-input>
        <div class="toolbar mb-15">
          <el-link type="primary" icon="Setting" @click="showSetting">
            &nbsp;{{ t('toolbar.advanced') }}
          </el-link>
          <el-link
            type="primary"
            :icon="isDark ? 'Sunny' : 'Moon'"
            @click="toggleDark()"
          >
            &nbsp;{{ isDark ? t('toolbar.light') : t('toolbar.dark') }}
          </el-link>
          <el-link type="primary" icon="ChatLineRound" @click="toggleLang">
            &nbsp;{{ t('toolbar.lang') }}
          </el-link>
          <el-link
            type="primary"
            icon="Link"
            href="https://github.com/youngfish42/PaperVault"
            target="_blank"
          >
            &nbsp;{{ t('toolbar.github') }}
          </el-link>
        </div>
        <el-alert
          :title="t('tips.title')"
          type="info"
          :center="true"
          :description="t('tips.desc')"
          :closable="false"
        />
      </div>
    </section>

    <!-- 已搜索：紧凑顶栏 + 三栏 -->
    <template v-else>
      <header class="pv-topbar">
        <div class="pv-container pv-topbar-inner">
          <a class="brand" href="/">{{ t('app.title') }}</a>
          <el-input
            v-model="searchContent.query"
            :placeholder="t('search.placeholder')"
            clearable
            @keyup.enter="search"
            size="default"
            class="pv-topbar-input"
          >
            <template #prepend>
              <el-select
                v-model="searchContent.searchtype"
                style="width: 100px"
                size="default"
              >
                <el-option
                  v-for="(itm, index) in SEARCH_TYPE_LIST"
                  :key="index"
                  :label="itm.label"
                  :value="itm.value"
                />
              </el-select>
            </template>
            <template #append>
              <el-button icon="Search" @click="search" />
            </template>
          </el-input>
          <div class="pv-topbar-actions">
            <el-link type="primary" icon="Setting" @click="showSetting">
              {{ t('toolbar.advanced') }}
            </el-link>
            <el-link
              type="primary"
              :icon="isDark ? 'Sunny' : 'Moon'"
              @click="toggleDark()"
            >
              {{ isDark ? t('toolbar.light') : t('toolbar.dark') }}
            </el-link>
            <el-link type="primary" icon="ChatLineRound" @click="toggleLang">
              {{ t('toolbar.lang') }}
            </el-link>
            <el-link
              type="primary"
              icon="Link"
              href="https://github.com/youngfish42/PaperVault"
              target="_blank"
            >
              {{ t('toolbar.github') }}
            </el-link>
          </div>
        </div>
      </header>

      <section class="pv-container pv-main">
        <aside class="pv-side pv-side-left">
          <ConfsTree :data="queryResult" @click="handleTreeClick" />
        </aside>
        <div class="pv-center">
          <SearchResultList
            ref="searchResult"
            @search-author="handleSearchAuthor"
          />
        </div>
        <aside class="pv-side pv-side-right">
          <GuessYourLike
            :loading="guessLoading"
            :result="guessList"
            @search-guess="handleSearchGuess"
          />
        </aside>
      </section>
    </template>

    <AdvancedSettingDlg
      ref="settingDlg"
      :data="searchContent"
      :confs="availableConfs"
      @update:data="handleUpdateForm"
    />
    <el-backtop :right="50" :bottom="50" />
  </main>
</template>

<style scoped>
/* 首屏 hero */
.pv-hero {
  width: 100%;
  min-height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px 0;
  box-sizing: border-box;
}
.pv-hero-inner {
  max-width: 760px;
}
.title {
  font-size: 56px;
  text-align: center;
  user-select: none;
  letter-spacing: 1px;
}
.title a {
  text-decoration: none;
  color: var(--el-text-color-primary, #333);
}
.title a:hover {
  text-decoration: underline;
}
.toolbar {
  text-align: center;
  user-select: none;
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 18px;
}

/* 搜索后顶栏 */
.pv-topbar {
  position: sticky;
  top: 0;
  z-index: 100;
  background: var(--el-bg-color, #fff);
  border-bottom: 1px solid var(--el-border-color-lighter, #ebeef5);
}
.pv-topbar-inner {
  display: flex;
  align-items: center;
  gap: 16px;
  padding-top: 10px;
  padding-bottom: 10px;
}
.brand {
  font-size: 20px;
  font-weight: 700;
  text-decoration: none;
  color: var(--el-text-color-primary, #333);
  white-space: nowrap;
}
.pv-topbar-input {
  flex: 1 1 auto;
  min-width: 320px;
  max-width: 720px;
}
.pv-topbar-actions {
  display: flex;
  gap: 14px;
  align-items: center;
  flex-shrink: 0;
}

/* 三栏主区 */
.pv-main {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr) 260px;
  gap: 16px;
  padding-top: 16px;
  padding-bottom: 24px;
  align-items: start;
}
.pv-side {
  position: sticky;
  top: 70px;
}
@media (max-width: 1200px) {
  .pv-main {
    grid-template-columns: 220px minmax(0, 1fr);
  }
  .pv-side-right {
    display: none;
  }
}
@media (max-width: 900px) {
  .pv-main {
    grid-template-columns: minmax(0, 1fr);
  }
  .pv-side {
    position: static;
    display: none;
  }
  .pv-topbar-inner {
    flex-wrap: wrap;
  }
  .pv-topbar-actions {
    width: 100%;
    justify-content: flex-end;
  }
}
</style>
