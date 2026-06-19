import { ref, computed, type ComputedRef } from 'vue'

export type Lang = 'zh' | 'en'

const STORAGE_KEY = 'papervault.lang'

const detect = (): Lang => {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'zh' || saved === 'en') return saved
  } catch {
    // ignore
  }
  const nav =
    (typeof navigator !== 'undefined' && navigator.language) || 'zh-CN'
  return nav.toLowerCase().startsWith('zh') ? 'zh' : 'en'
}

const lang = ref<Lang>(detect())

const messages: Record<Lang, Record<string, string>> = {
  zh: {
    'app.title': 'PaperVault',
    'search.placeholder': '输入关键词搜索论文（标题 / 作者）',
    'search.type.title': '按标题',
    'search.type.author': '按作者',
    'search.button': '搜索',
    'search.warn.empty': '请先输入搜索关键词',
    'toolbar.advanced': '高级筛选',
    'toolbar.dark': '深色模式',
    'toolbar.light': '浅色模式',
    'toolbar.github': 'GitHub',
    'toolbar.lang': 'English',
    'tips.title': '搜索小贴士',
    'tips.desc':
      '① 默认按标题匹配，可通过左侧下拉切换为按作者搜索；② 多关键词建议使用空格分隔，可获得更精准结果；③ 点击「高级筛选」可按年份、特定作者、会议范围进一步过滤；④ 点击作者名可一键检索其所有论文。',
    'result.sortBy': '排序：',
    'result.sort.year': '年份（新→旧）',
    'result.sort.yearAsc': '年份（旧→新）',
    'result.sort.conf': '会议',
    'result.sort.title': '标题',
    'result.export.txt': '导出 TXT',
    'result.export.csv': '导出 CSV',
    'result.empty': '暂无搜索结果',
    'result.abstract': '摘要',
    'result.code': '代码',
    'result.openPaper': '原文链接',
    'result.copyTitle': '复制标题',
    'result.copied': '已复制',
    'result.filter.title': '结果过滤',
    'result.filter.searchWithin': '在结果中过滤',
    'result.filter.searchWithinPh': '输入关键词进一步筛选标题 / 作者 / 摘要',
    'result.filter.hasAbstract': '仅看含摘要',
    'result.filter.hasCode': '仅看含代码',
    'result.filter.yearRange': '年份范围',
    'result.filter.field': '研究领域',
    'result.filter.fieldAll': '不限',
    'result.filter.matched': '匹配 {n} / {total}',
    'result.filter.reset': '清除筛选',
    'result.authors': '作者',
    'result.more': '展开',
    'result.less': '收起',
    'result.noAbstract': '该论文暂无摘要',
    'result.delete': '从结果中移除',
    'guess.header': '猜你想搜（DeepSeek）',
    'guess.empty': '暂无推荐关键词',
    'tree.all': '全部',
    'dlg.title': '高级筛选',
    'dlg.years': '时间范围',
    'dlg.specificYear': '指定年份',
    'dlg.specificYearPh': '例如 2024',
    'dlg.specificAuthor': '指定作者',
    'dlg.specificAuthorPh': '输入作者名',
    'dlg.confs': '会议范围',
    'dlg.checkAll': '全选',
    'dlg.checkInvert': '反选',
    'dlg.reset': '重置',
    'dlg.done': '确定',
    'year.since': '自 {year} 起',
    'year.all': '不限'
  },
  en: {
    'app.title': 'PaperVault',
    'search.placeholder': 'Search papers by title or author',
    'search.type.title': 'Title',
    'search.type.author': 'Author',
    'search.button': 'Search',
    'search.warn.empty': 'Please input your keywords for search.',
    'toolbar.advanced': 'Advanced',
    'toolbar.dark': 'Dark',
    'toolbar.light': 'Light',
    'toolbar.github': 'GitHub',
    'toolbar.lang': '中文',
    'tips.title': 'Search Tips',
    'tips.desc':
      '① Default field is title; switch to "Author" via the prefix selector. ② Use spaces to combine multiple keywords for more precise hits. ③ Open "Advanced" to narrow by year range, specific author or conference subset. ④ Click any author name to instantly list all their papers.',
    'result.sortBy': 'Sort:',
    'result.sort.year': 'Year (new→old)',
    'result.sort.yearAsc': 'Year (old→new)',
    'result.sort.conf': 'Venue',
    'result.sort.title': 'Title',
    'result.export.txt': 'Export TXT',
    'result.export.csv': 'Export CSV',
    'result.empty': 'No search result',
    'result.abstract': 'Abstract',
    'result.code': 'CODE',
    'result.openPaper': 'Open paper',
    'result.copyTitle': 'Copy title',
    'result.copied': 'Copied',
    'result.filter.title': 'Refine results',
    'result.filter.searchWithin': 'Filter within results',
    'result.filter.searchWithinPh':
      'Match keywords in title / authors / abstract',
    'result.filter.hasAbstract': 'Has abstract',
    'result.filter.hasCode': 'Has code',
    'result.filter.yearRange': 'Year range',
    'result.filter.field': 'Research field',
    'result.filter.fieldAll': 'Any',
    'result.filter.matched': 'Showing {n} of {total}',
    'result.filter.reset': 'Clear filters',
    'result.authors': 'Authors',
    'result.more': 'Show more',
    'result.less': 'Show less',
    'result.noAbstract': 'No abstract available',
    'result.delete': 'Remove from results',
    'guess.header': 'Guess you like (DeepSeek)',
    'guess.empty': 'No suggestion yet',
    'tree.all': 'All',
    'dlg.title': 'Advanced Setting',
    'dlg.years': 'Years',
    'dlg.specificYear': 'Specific Year',
    'dlg.specificYearPh': 'e.g. 2024',
    'dlg.specificAuthor': 'Specific Author',
    'dlg.specificAuthorPh': 'Input a specific author',
    'dlg.confs': 'Confs',
    'dlg.checkAll': 'Check All',
    'dlg.checkInvert': 'Check Invert',
    'dlg.reset': 'Reset',
    'dlg.done': 'Done',
    'year.since': 'Since {year}',
    'year.all': 'All'
  }
}

const format = (
  tpl: string,
  vars?: Record<string, string | number>
): string => {
  if (!vars) return tpl
  return tpl.replace(/\{(\w+)\}/g, (_m, k) =>
    vars[k] === undefined ? `{${k}}` : String(vars[k])
  )
}

export const useI18n = (): {
  lang: typeof lang
  t: (key: string, vars?: Record<string, string | number>) => string
  toggle: () => void
  isZh: ComputedRef<boolean>
} => {
  const t = (key: string, vars?: Record<string, string | number>): string => {
    const dict = messages[lang.value] || messages.zh
    const tpl = dict[key] ?? messages.zh[key] ?? key
    return format(tpl, vars)
  }
  const toggle = (): void => {
    lang.value = lang.value === 'zh' ? 'en' : 'zh'
    try {
      localStorage.setItem(STORAGE_KEY, lang.value)
    } catch {
      // ignore
    }
  }
  const isZh = computed(() => lang.value === 'zh')
  return { lang, t, toggle, isZh }
}

export default useI18n
