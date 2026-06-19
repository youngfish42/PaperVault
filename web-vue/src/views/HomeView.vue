<script setup lang="ts">
import { onMounted, reactive, ref, shallowRef } from 'vue'
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

const SEARCH_TYPE_LIST = [
  { label: 'Title', value: 'title' },
  { label: 'Author', value: 'author' }
]

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
    size: 500
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
    params.conf = searchContent.confs.join(',')
  }
  return params
}

const search = (): void => {
  if (searchContent.query === '' && searchContent.sp_author === '') {
    ElMessage.warning('Please input your keywords for search.')
    return
  }
  const loading = ElLoading.service({ lock: true, text: 'Searching...' })
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

const handleTreeClick = (data: { level: number; key?: string; parent?: string }): void => {
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
    <el-row
      justify="center"
      :class="['mb-15 pos-absolute', firstEntry ? 'first-entry' : 'normal']"
    >
      <el-col class="gutter-20" :xs="24" :sm="16" :md="14" :lg="10" :xl="8">
        <h1 class="title mb-15"><a href="/">PaperVault</a></h1>
        <!-- Search Bar -->
        <el-input
          v-model="searchContent.query"
          placeholder="Input your keywords"
          clearable
          @keyup.enter="search"
          size="large"
          class="mb-10"
        >
          <template #prepend>
            <el-select
              v-model="searchContent.searchtype"
              placeholder="Select"
              style="width: 100px"
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
            <el-button icon="Search" @click="search" />
          </template>
        </el-input>
        <!-- Toolbar -->
        <div class="toolbar mb-15">
          <el-link type="primary" icon="Setting" @click="showSetting">
            &nbsp;Advanced setting
          </el-link>
          <el-link
            type="primary"
            :icon="isDark ? 'Sunny' : 'Moon'"
            @click="toggleDark()"
          >
            &nbsp;{{ `${isDark ? 'Light' : 'Dark'}Mode` }}
          </el-link>
          <el-link
            type="primary"
            icon="Link"
            href="https://github.com/youngfish42/PaperVault"
            target="_blank"
          >
            &nbsp;GitHub
          </el-link>
        </div>
        <!-- Tips -->
        <el-alert
          title="Tips!"
          type="info"
          :center="true"
          description="You can get more precise results by using advanced setting. If this project is helpful to you, please give us a ⭐star!"
        />
      </el-col>
    </el-row>
    <el-row justify="center" v-show="!firstEntry">
      <el-col class="gutter-20" :xs="24" :sm="16" :md="5" :lg="4" :xl="3">
        <!-- Select tree -->
        <ConfsTree :data="queryResult" @click="handleTreeClick" />
      </el-col>
      <el-col class="gutter-20" :xs="24" :sm="16" :md="14" :lg="10" :xl="8">
        <!-- Search result list -->
        <SearchResultList
          ref="searchResult"
          @search-author="handleSearchAuthor"
        />
      </el-col>
      <el-col class="gutter-20" :xs="24" :sm="16" :md="5" :lg="4" :xl="3">
        <GuessYourLike
          :loading="guessLoading"
          :result="guessList"
          @search-guess="handleSearchGuess"
        />
      </el-col>
    </el-row>

    <!-- Advanced setting dialog -->
    <AdvancedSettingDlg
      ref="settingDlg"
      v-model:data="searchContent"
      :confs="availableConfs"
    />
    <!-- Back to top -->
    <el-backtop :right="50" :bottom="50" />
    <!-- Copy right -->
    <div :class="['copy-right mb-15', firstEntry ? 'copy-first-entry' : '']">
      <a href="https://beian.miit.gov.cn/" target="_blank">
        <img src="@/assets/beian.png" />浙ICP备2023002681号-1
      </a>
    </div>
  </main>
</template>

<style scoped>
.title {
  font-size: 60px;
  text-align: center;
  user-select: none;
}

.title a {
  text-decoration: none;
  color: #333;
}

.title a:hover {
  text-decoration: underline;
}
.toolbar {
  text-align: center;
  user-select: none;
}
.toolbar a + a {
  margin-left: 20px;
}
.gutter-20 {
  padding: 0 20px;
}
.copy-right {
  text-align: center;
  font-size: 14px;
  font-weight: 500;
  line-height: 16px;
}
.copy-right a {
  text-decoration: none;
  color: #999;
}
.copy-right a > * {
  vertical-align: middle;
}
.copy-right a img {
  width: 14px;
  margin-right: 5px;
}
.first-entry {
  top: calc(50% - 80px);
  transform: translateY(-50%);
}
.normal {
  top: 0;
  transform: translateY(0);
}
.copy-first-entry {
  position: fixed;
  bottom: 20px;
  left: 50%;
  transform: translateX(-50%);
}
</style>
