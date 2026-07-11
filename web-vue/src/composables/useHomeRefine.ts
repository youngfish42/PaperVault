import { ref, type Ref } from 'vue'
import type SearchResultList from '@/components/SearchResultList.vue'

type SearchResultRef = InstanceType<typeof SearchResultList> | null

export function useHomeRefine(options: {
  onSearch: () => void
  searchResultRef: Ref<SearchResultRef>
}) {
  const { onSearch, searchResultRef } = options
  const refineInResults = ref(false)
  const refineKeyword = ref('')

  const applyRefineFromTop = (): void => {
    searchResultRef.value?.setRefineKeyword?.(refineKeyword.value)
  }

  const handleTopEnter = (): void => {
    if (refineInResults.value) {
      applyRefineFromTop()
    } else {
      onSearch()
    }
  }

  const handleTopAction = (): void => {
    handleTopEnter()
  }

  const onToggleRefineMode = (val: string | number | boolean): void => {
    if (!val) {
      refineKeyword.value = ''
      searchResultRef.value?.clearRefineKeyword?.()
    }
  }

  return {
    refineInResults,
    refineKeyword,
    applyRefineFromTop,
    handleTopEnter,
    handleTopAction,
    onToggleRefineMode
  }
}

export default useHomeRefine
