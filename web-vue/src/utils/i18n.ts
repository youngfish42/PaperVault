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
    'app.slogan': '顶尖计算机论文精选，深度可搜。',
    'search.placeholder':
      '输入关键词或检索式，例如 federated AND (privacy OR fairness) AU="Yang Liu" PY=2024-2026',
    'search.placeholder.short': '搜索论文、作者、会议……',
    'search.button': '搜索',
    'search.tab.smart': '智能搜索',
    'search.tab.advanced': '高级搜索',
    'search.heroHint': '需要按字段限定或可视化组合多条件？前往 ',
    'search.heroHintLink': '高级搜索',
    'search.heroHintTail': '。',
    'search.cheatsheetToggle.show': '查看检索语法',
    'search.cheatsheetToggle.hide': '收起语法说明',
    'search.warn.empty': '请先输入搜索关键词',
    'search.warn.truncated':
      '结果过多，仅展示前 {n} 条，请使用更精确的关键词或筛选条件',
    'search.refinePlaceholder': '在当前结果中过滤（标题 / 作者 / 摘要 / 会议）',
    'search.refinePrepend': '结果内',
    'search.toggle.label': '结果内过滤',
    'search.toggle.offTip': '当前：全库检索。回车将向后端发起新搜索。',
    'search.toggle.onTip':
      '当前：结果内过滤。输入仅在已加载的结果中匹配，不会发起后端检索。',
    'search.dslHint':
      '支持 Web of Science 风格高级语法（TS / TI / AB / AU / SO / PY、AND / OR / NOT、NEAR/x、引号短语）。鼠标移到上方查看示例。',
    'search.dslTipHtml':
      '<div class="pv-syntax">' +
      '<div class="pv-syntax-title">检索语法速查</div>' +
      '<div class="pv-syntax-section">' +
      '<div class="pv-syntax-section-title">① 字段标签</div>' +
      '<div class="pv-syntax-section-desc">使用 <code>=</code> 或 <code>:</code> 连接（大小写不敏感）</div>' +
      '<table class="pv-syntax-table">' +
      '<tr><th>标签</th><th>含义</th><th>示例</th></tr>' +
      '<tr><td><code>TS</code></td><td>主题（默认）</td><td><code>TS=federated</code></td></tr>' +
      '<tr><td><code>TI</code></td><td>标题</td><td><code>TI=diffusion</code></td></tr>' +
      '<tr><td><code>AB</code></td><td>摘要</td><td><code>AB=privacy</code></td></tr>' +
      '<tr><td><code>AU</code></td><td>作者</td><td><code>AU="Yann LeCun"</code></td></tr>' +
      '<tr><td><code>SO</code></td><td>会议</td><td><code>SO=ICLR,NeurIPS</code></td></tr>' +
      '<tr><td><code>PY</code></td><td>年份</td><td><code>PY=2023-2026</code></td></tr>' +
      '</table>' +
      '</div>' +
      '<div class="pv-syntax-section">' +
      '<div class="pv-syntax-section-title">② 布尔运算</div>' +
      '<div class="pv-syntax-chip-row">' +
      '<span class="pv-syntax-chip"><code>AND</code> 同时满足</span>' +
      '<span class="pv-syntax-chip"><code>OR</code> 任一满足</span>' +
      '<span class="pv-syntax-chip"><code>NOT</code> 排除</span>' +
      '<span class="pv-syntax-chip pv-syntax-chip--muted">空格 = 隐式 AND</span>' +
      '</div>' +
      '<div class="pv-syntax-section-desc">优先级：<code>NEAR</code> &gt; <code>NOT</code> &gt; <code>AND</code> &gt; <code>OR</code>，可用 <code>( )</code> 改变结合顺序。</div>' +
      '</div>' +
      '<div class="pv-syntax-section">' +
      '<div class="pv-syntax-section-title">③ 短语 / 邻近 / 范围</div>' +
      '<div class="pv-syntax-grid">' +
      '<div><span class="pv-syntax-key">完整短语</span><code>"federated learning"</code></div>' +
      '<div><span class="pv-syntax-key">邻近匹配</span><code>privacy NEAR/5 utility</code></div>' +
      '<div><span class="pv-syntax-key">多值列表</span><code>SO=ICLR,NeurIPS</code></div>' +
      '<div><span class="pv-syntax-key">数值范围</span><code>PY=2023-2026</code></div>' +
      '</div>' +
      '</div>' +
      '<div class="pv-syntax-example">' +
      '<div class="pv-syntax-example-label">完整示例</div>' +
      '<code>TS=(federated AND (privacy OR fairness)) NOT survey SO=ICLR,NeurIPS PY=2024-2026</code>' +
      '</div>' +
      '</div>',
    'toolbar.advanced': '高级搜索',
    'toolbar.dark': '深色模式',
    'toolbar.light': '浅色模式',
    'toolbar.github': 'GitHub',
    'toolbar.lang': 'English',
    'toolbar.settings': '设置',
    'settings.pageTitle': '设置',
    'settings.intro':
      '管理 PaperVault 的偏好与配置，更多分组将在后续版本上线。',
    'settings.wip': '即将在 P2-D 版本提供',
    'settings.aiSuggest.title': 'AI 关键词推荐',
    'settings.aiSuggest.desc':
      '在此选择用于生成相关关键词的 LLM 提供方与模型，配置入口将在 P2-D 开放。',
    'settings.about.title': '关于此页面',
    'settings.about.body':
      '本页是 P2-C 阶段交付的设置面板外壳，负责承载 P2-D 接入的 AI 提供方配置、模型选择与默认参数。',
    'tips.title': '搜索小贴士',
    'tips.desc':
      '① 直接输入关键词即可，默认按主题（标题 + 摘要 + 关键词）匹配；② 可使用 Web of Science 风格语法精确限定字段，如 AU="Yang Liu" SO=ICLR,NeurIPS PY=2023-2026；③ 想要可视化组合多条件？点击「高级搜索」打开行式表单，自动生成检索式；④ 点击作者名可一键检索其所有论文。',
    'result.sortBy': '排序：',
    'result.sort.yearDesc': '年份（新→旧）',
    'result.sort.yearAsc': '年份（旧→新）',
    'result.sort.confAsc': '会议（A→Z）',
    'result.sort.confDesc': '会议（Z→A）',
    'result.sort.titleAsc': '标题（A→Z）',
    'result.sort.titleDesc': '标题（Z→A）',
    'result.export.txt': '导出 TXT',
    'result.export.csv': '导出 CSV',
    'result.empty': '暂无搜索结果',
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
    'tree.truncatedMark': '· 仅展示 {n}',
    'tree.truncatedHint':
      '匹配过多，仅展示前 {n} 条用于浏览，可缩小关键词获取完整结果。',
    'year.since': '自 {year} 起',
    'year.all': '不限',
    'adv.pageTitle': '高级搜索',
    'adv.builder.title': '检索条件',
    'adv.builder.hint': '按行添加字段条件，使用 AND / OR / NOT 组合',
    'adv.builder.firstRow': '检索式',
    'adv.builder.valuePh': '关键词或短语（短语请加引号）',
    'adv.builder.addRow': '新增一行',
    'adv.builder.removeRow': '删除该行',
    'adv.field.topic': '主题（TS）',
    'adv.field.title': '标题（TI）',
    'adv.field.abstract': '摘要（AB）',
    'adv.field.author': '作者（AU）',
    'adv.field.conf': '会议（SO）',
    'adv.field.year': '年份（PY）',
    'adv.yearRange': '年份范围（PY）',
    'adv.yearFromPh': '起始年，如 2020',
    'adv.yearToPh': '截止年，如 2026',
    'adv.preview': '生成的检索式',
    'adv.previewEmpty': '（请先填写至少一行条件）',
    'adv.clear': '清空',
    'adv.search': '搜索',
    'adv.warn.empty': '请先填写至少一行检索条件',
    'adv.cheatsheet.title': '语法说明',
    'adv.cheatsheet.confs': '当前已收录会议（可用于 SO= 字段）'
  },
  en: {
    'app.title': 'PaperVault',
    'app.slogan': 'Curated top-tier CS papers. Deeply searchable.',
    'search.placeholder':
      'Enter keywords or an expression, e.g. federated AND (privacy OR fairness) AU="Yang Liu" PY=2024-2026',
    'search.placeholder.short': 'Search papers, authors, venues...',
    'search.button': 'Search',
    'search.tab.smart': 'Smart Search',
    'search.tab.advanced': 'Advanced Search',
    'search.heroHint':
      'To search specific indexes or fields, or build a query, go to ',
    'search.heroHintLink': 'Advanced Search',
    'search.heroHintTail': '.',
    'search.cheatsheetToggle.show': 'Show query syntax',
    'search.cheatsheetToggle.hide': 'Hide syntax help',
    'search.warn.empty': 'Please input your keywords for search.',
    'search.warn.truncated':
      'Too many results; showing the first {n}. Narrow your query or apply filters.',
    'search.refinePlaceholder':
      'Filter loaded results (title / authors / abstract / venue)',
    'search.refinePrepend': 'In results',
    'search.toggle.label': 'Filter in results',
    'search.toggle.offTip':
      'Now: full-corpus search. Pressing Enter will issue a new backend query.',
    'search.toggle.onTip':
      'Now: in-results filter. Input matches loaded results only; no backend request is sent.',
    'search.dslHint':
      'Web of Science style syntax: TS / TI / AB / AU / SO / PY, AND / OR / NOT, NEAR/x, quoted phrases. Hover for examples.',
    'search.dslTipHtml':
      '<div class="pv-syntax">' +
      '<div class="pv-syntax-title">Query syntax</div>' +
      '<div class="pv-syntax-section">' +
      '<div class="pv-syntax-section-title">① Field tags</div>' +
      '<div class="pv-syntax-section-desc">Join with <code>=</code> or <code>:</code> (case-insensitive)</div>' +
      '<table class="pv-syntax-table">' +
      '<tr><th>Tag</th><th>Meaning</th><th>Example</th></tr>' +
      '<tr><td><code>TS</code></td><td>Topic (default)</td><td><code>TS=federated</code></td></tr>' +
      '<tr><td><code>TI</code></td><td>Title</td><td><code>TI=diffusion</code></td></tr>' +
      '<tr><td><code>AB</code></td><td>Abstract</td><td><code>AB=privacy</code></td></tr>' +
      '<tr><td><code>AU</code></td><td>Author</td><td><code>AU="Yann LeCun"</code></td></tr>' +
      '<tr><td><code>SO</code></td><td>Venue</td><td><code>SO=ICLR,NeurIPS</code></td></tr>' +
      '<tr><td><code>PY</code></td><td>Year</td><td><code>PY=2023-2026</code></td></tr>' +
      '</table>' +
      '</div>' +
      '<div class="pv-syntax-section">' +
      '<div class="pv-syntax-section-title">② Boolean operators</div>' +
      '<div class="pv-syntax-chip-row">' +
      '<span class="pv-syntax-chip"><code>AND</code> all match</span>' +
      '<span class="pv-syntax-chip"><code>OR</code> any match</span>' +
      '<span class="pv-syntax-chip"><code>NOT</code> exclude</span>' +
      '<span class="pv-syntax-chip pv-syntax-chip--muted">space = implicit AND</span>' +
      '</div>' +
      '<div class="pv-syntax-section-desc">Precedence: <code>NEAR</code> &gt; <code>NOT</code> &gt; <code>AND</code> &gt; <code>OR</code>. Use <code>( )</code> to group.</div>' +
      '</div>' +
      '<div class="pv-syntax-section">' +
      '<div class="pv-syntax-section-title">③ Phrase / Proximity / Range</div>' +
      '<div class="pv-syntax-grid">' +
      '<div><span class="pv-syntax-key">Phrase</span><code>"federated learning"</code></div>' +
      '<div><span class="pv-syntax-key">Proximity</span><code>privacy NEAR/5 utility</code></div>' +
      '<div><span class="pv-syntax-key">List</span><code>SO=ICLR,NeurIPS</code></div>' +
      '<div><span class="pv-syntax-key">Range</span><code>PY=2023-2026</code></div>' +
      '</div>' +
      '</div>' +
      '<div class="pv-syntax-example">' +
      '<div class="pv-syntax-example-label">Full example</div>' +
      '<code>TS=(federated AND (privacy OR fairness)) NOT survey SO=ICLR,NeurIPS PY=2024-2026</code>' +
      '</div>' +
      '</div>',
    'toolbar.advanced': 'Advanced search',
    'toolbar.dark': 'Dark',
    'toolbar.light': 'Light',
    'toolbar.github': 'GitHub',
    'toolbar.lang': '中文',
    'toolbar.settings': 'Settings',
    'settings.pageTitle': 'Settings',
    'settings.intro':
      'Manage PaperVault preferences and configuration. More sections will be added in upcoming releases.',
    'settings.wip': 'Coming in P2-D',
    'settings.aiSuggest.title': 'AI keyword suggestions',
    'settings.aiSuggest.desc':
      'Choose the LLM provider and model used to generate related keywords. Configuration UI ships in P2-D.',
    'settings.about.title': 'About this page',
    'settings.about.body':
      'This page is the P2-C settings panel shell. It will host the P2-D AI provider configuration, model selection, and default-parameter controls.',
    'tips.title': 'Search Tips',
    'tips.desc':
      '① Just type keywords — the default Topic search matches title + abstract + author keywords. ② Use Web of Science style field tags for precision, e.g. AU="Yang Liu" SO=ICLR,NeurIPS PY=2023-2026. ③ Need a visual builder? Open "Advanced search" to add rows and auto-generate the expression. ④ Click any author name to instantly list all their papers.',
    'result.sortBy': 'Sort:',
    'result.sort.yearDesc': 'Year (new→old)',
    'result.sort.yearAsc': 'Year (old→new)',
    'result.sort.confAsc': 'Venue (A→Z)',
    'result.sort.confDesc': 'Venue (Z→A)',
    'result.sort.titleAsc': 'Title (A→Z)',
    'result.sort.titleDesc': 'Title (Z→A)',
    'result.export.txt': 'Export TXT',
    'result.export.csv': 'Export CSV',
    'result.empty': 'No search result',
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
    'tree.truncatedMark': '· showing {n}',
    'tree.truncatedHint':
      'Too many matches; only the first {n} are listed for browsing. Narrow the query for full results.',
    'year.since': 'Since {year}',
    'year.all': 'All',
    'adv.pageTitle': 'Advanced search',
    'adv.builder.title': 'Query builder',
    'adv.builder.hint': 'Add rows and combine them with AND / OR / NOT',
    'adv.builder.firstRow': 'Query',
    'adv.builder.valuePh': 'Keyword or phrase (use quotes for phrases)',
    'adv.builder.addRow': 'Add row',
    'adv.builder.removeRow': 'Remove row',
    'adv.field.topic': 'Topic (TS)',
    'adv.field.title': 'Title (TI)',
    'adv.field.abstract': 'Abstract (AB)',
    'adv.field.author': 'Author (AU)',
    'adv.field.conf': 'Venue (SO)',
    'adv.field.year': 'Year (PY)',
    'adv.yearRange': 'Year range (PY)',
    'adv.yearFromPh': 'From, e.g. 2020',
    'adv.yearToPh': 'To, e.g. 2026',
    'adv.preview': 'Generated expression',
    'adv.previewEmpty': '(Fill at least one row to preview)',
    'adv.clear': 'Clear',
    'adv.search': 'Search',
    'adv.warn.empty': 'Please fill in at least one query row.',
    'adv.cheatsheet.title': 'Syntax reference',
    'adv.cheatsheet.confs': 'Indexed venues (usable in SO=)'
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
