<script setup lang="ts">
import { onMounted, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDark, useToggle } from '@vueuse/core'
import SearchResultList from '@/components/SearchResultList.vue'
import HomeHero from '@/components/home/HomeHero.vue'
import HomeResultsLayout from '@/components/home/HomeResultsLayout.vue'
import MainNavBar from '@/components/MainNavBar.vue'
import { buildOrMerge } from '@/utils/queryMerge'
import { useI18n } from '@/utils/i18n'
import { useHomeSearch } from '@/composables/useHomeSearch'
import { useHomeRefine } from '@/composables/useHomeRefine'

const { toggle: toggleLang } = useI18n()
const route = useRoute()
const router = useRouter()

const home = useHomeSearch()
const {
  firstEntry,
  searchContent,
  queryResult,
  searchMeta,
  activeAst,
  originalTopic,
  guessLoading,
  guessList,
  guessProviderLabel
} = home
const search = home.search
const handleTreeClick = home.handleTreeClick

const resultsLayout = ref<InstanceType<typeof HomeResultsLayout> | null>(null)
const searchResult = shallowRef<InstanceType<typeof SearchResultList> | null>(
  null
)
watch(
  () => resultsLayout.value?.searchResult ?? null,
  v => {
    searchResult.value = v
  }
)
home.setSearchResultRef(searchResult)

const refine = useHomeRefine({
  onSearch: search,
  searchResultRef: searchResult
})
const {
  refineInResults,
  refineKeyword,
  applyRefineFromTop,
  handleTopEnter,
  handleTopAction,
  onToggleRefineMode
} = refine

const resetRefineForNewSearch = (): void => {
  refineInResults.value = false
  refineKeyword.value = ''
  searchResult.value?.clearRefineKeyword?.()
}

const handleSearchAuthor = (data: string): void => {
  searchContent.query = `AU="${data}"`
  searchContent.sp_author = ''
  resetRefineForNewSearch()
  search()
}

const handleSearchGuess = (data: string): void => {
  searchContent.query = data
  originalTopic.value = data
  resetRefineForNewSearch()
  search()
}

const handleSearchGuessMany = (picked: string[]): void => {
  if (!picked.length) return
  searchContent.query = buildOrMerge(searchContent.query, picked)
  resetRefineForNewSearch()
  search()
}

const handleAiSearchPick = (payload: {
  query: string
  rerank: boolean
  seed: string
}): void => {
  searchContent.query = payload.query
  originalTopic.value = payload.seed
  resetRefineForNewSearch()
  search()
}

const goAdvanced = (): void => {
  router.push({ path: '/advanced' })
}

const goSettings = (): void => {
  router.push({ path: '/settings' })
}

const isDark = useDark()
const toggleDark = useToggle(isDark)

watch(
  () => route.query.q,
  () => home.consumeQueryParam(route)
)

onMounted(async () => {
  await home.initConfs()
  home.consumeQueryParam(route)
})
</script>

<template>
  <main class="full pos-relative">
    <MainNavBar
      active-key="home"
      :is-dark="isDark"
      @toggle-dark="toggleDark()"
    />

    <HomeHero
      v-if="firstEntry"
      v-model:query="searchContent.query"
      @search="search"
      @ai-pick="handleAiSearchPick"
    />

    <HomeResultsLayout
      v-else
      ref="resultsLayout"
      v-model:query="searchContent.query"
      v-model:refine-in-results="refineInResults"
      v-model:refine-keyword="refineKeyword"
      :query-result="queryResult"
      :search-meta="searchMeta"
      :active-ast="activeAst"
      :guess-list="guessList"
      :guess-loading="guessLoading"
      :guess-provider-label="guessProviderLabel"
      :is-dark="isDark"
      @top-enter="handleTopEnter"
      @top-action="handleTopAction"
      @apply-refine="applyRefineFromTop"
      @toggle-refine-mode="onToggleRefineMode"
      @search-author="handleSearchAuthor"
      @tree-click="handleTreeClick"
      @guess-replace="handleSearchGuess"
      @guess-many="handleSearchGuessMany"
      @toggle-dark="toggleDark()"
      @toggle-lang="toggleLang"
      @go-advanced="goAdvanced"
      @go-settings="goSettings"
    />

    <el-backtop :right="50" :bottom="50" />
  </main>
</template>
