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
    'search.mode.label': '搜索方式',
    'search.mode.standard': '普通搜索',
    'app.title': 'PaperVault',
    'app.slogan': '顶尖计算机论文精选，深度可搜。',
    'search.placeholder':
      '输入关键词或检索式，例如 federated AND (privacy OR fairness) AU="Yang Liu" PY=2024-2026',
    'search.placeholder.short': '搜索论文、作者、会议……',
    'search.button': '搜索',
    'search.tab.smart': '智能搜索',
    'search.tab.advanced': '高级搜索',
    'search.heroHint': '直接输入关键词开始搜索；需要组合字段和条件时，',
    'search.heroHintLink': '使用高级搜索',
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
    'toolbar.docs': '开发者文档',
    'docs.title': '搜索 SDK 文档',
    'docs.intro':
      '用 REST API 和查询 DSL 将 PaperVault 的高级搜索接入你的脚本、Notebook 或应用。',
    'docs.quickstart': '快速开始',
    'docs.quickstartText':
      '论文搜索无需登录或 API 密钥。使用 GET 请求并传入 q、page 和 page_size 参数即可。',
    'docs.dsl': '查询 DSL',
    'docs.dslText':
      '支持 TS、TI、AB、AU、SO、PY 字段，以及 AND、OR、NOT、NEAR/x 和引号短语。',
    'docs.example': 'cURL 示例',
    'docs.copy': '复制',
    'docs.copied': '已复制',
    'docs.copyFailed': '复制失败，请手动选择文本',
    'docs.responses': '响应与分页',
    'docs.responsesText':
      '成功响应包含 items 和 meta。page 从 1 开始，page_size 受服务端上限约束；错误统一返回 error、message 和 request_id。',
    'docs.ai': 'AI 接口（可选）',
    'docs.aiText':
      '关键词推荐使用 POST /api/v1/suggest，结果重排使用 POST /api/v1/ai/rerank；密钥通过请求体传入或使用服务端配置。',
    'docs.source': '完整参数与示例：',
    'docs.skillTitle': '下载 PaperVault 搜索 Skill',
    'docs.skillText':
      '请先下载安装 PaperVault Search Skill，并完成初始化配置。下载 ZIP 后解压到本地，按包内说明接入支持 MCP 的智能体客户端。',
    'docs.skillDownload': '下载 Skill ZIP',
    'docs.skillUrl':
      '下载地址：https://papervault.top/downloads/papervault-search-skill.zip',
    'settings.pageTitle': '设置',
    'settings.intro':
      '本页配置「AI 关键词推荐」与「AI 结果重排」所需的 LLM 提供方、API 密钥和默认参数。除 API 密钥保存在会话中（关闭浏览器即清除）外，其余设置保存在浏览器本地；留空时将使用服务端默认环境变量。',
    'settings.aiSuggest.title': 'AI 关键词推荐',
    'settings.aiSuggest.description':
      '下方的参数会用于首页搜索框的「AI 搜索」按钮，以及搜索结果右侧的 AI 推荐面板。选择提供方后会自动填入默认接口地址、模型与协议；你可以在此基础上修改，也可以完全留空以使用服务端默认配置。',
    'settings.aiSuggest.form.provider': '提供方',
    'settings.aiSuggest.form.baseUrl': '接口地址',
    'settings.aiSuggest.form.model': '模型',
    'settings.aiSuggest.form.apiKey': 'API 密钥',
    'settings.aiSuggest.form.apiKeyPh': '留空则使用服务端环境变量',
    'settings.aiSuggest.form.protocol': '协议（自动）',
    'settings.aiSuggest.form.temperature': '温度',
    'settings.aiSuggest.form.maxKeywords': '关键词数',
    'settings.aiSuggest.form.maxTokens': '最大输出 token',
    'settings.aiSuggest.action.save': '保存',
    'settings.aiSuggest.action.clear': '清除',
    'settings.aiSuggest.saved': '✓ 已保存',
    'settings.aiSuggest.hint.apiKey':
      '密钥仅存于本会话（关闭浏览器即清除），不会上传到服务器。',
    'settings.aiSuggest.hint.provider':
      '选择提供方后会自动填入默认接口地址、模型与协议，你仍可手动修改。',
    'settings.aiSuggest.test.title': '功能测试',
    'settings.aiSuggest.test.description':
      '输入一段研究描述，点击「生成关键词」，验证当前配置能否正常调用 LLM。',
    'settings.aiSuggest.test.queryPh': '输入一段描述…',
    'settings.aiSuggest.test.button': '生成关键词',
    'settings.aiSuggest.test.empty': '暂无返回关键词',
    'settings.aiSuggest.test.errorQuery': '请输入测试用的查询内容',
    'settings.aiSuggest.test.errorProvider': '请先选择提供方',
    'settings.aiSuggest.test.errorUnknown': '请求失败',
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
    'guess.header': 'AI 关键词推荐',
    'guess.provider': '来自 {provider} · {ms}ms',
    'guess.empty': '暂无推荐关键词',
    'guess.replace': '仅搜索此词',
    'guess.merge': '加入查询（OR）',
    'search.aiSearch.button': 'AI 搜索',
    'search.aiSearch.dialogTitle': 'AI 搜索（关键词 + 重排）',
    'search.aiSearch.seedPh': '描述你想搜的主题，比如"time series llm"',
    'search.aiSearch.run': '生成',
    'search.aiSearch.rerank': '对结果按 AI 相关度重排',
    'search.aiSearch.empty': '暂无推荐',
    'search.aiSearch.toastFail': 'AI 搜索失败：{msg}',
    'search.aiSearch.toastFailGeneric': '请求失败',
    'search.aiSearch.toastNoSeed': '请先输入搜索主题',
    'search.aiSearch.toastNoKey': '请先在「设置」配置 API key',
    'search.aiSearch.goSettings': '前往设置',
    'search.aiSearch.hint': '用 AI 扩展关键词（可选）',
    'search.aiSearch.mergeCapHint': '仅合并前 {cap} 个，避免搜索范围过宽',
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
    'adv.cheatsheet.confs': '当前已收录会议（可用于 SO= 字段）',
    'adv.copy': '复制',
    'adv.copyOk': '检索式已复制',
    'adv.copyFail': '复制失败，请手动选择文本',
    'adv.import.label': '从文本导入检索式',
    'adv.import.placeholder':
      '粘贴文本检索式，如 TS=(federated OR privacy) AU="Yang Liu" PY=2024-2026',
    'adv.import.apply': '解析到构建器',
    'adv.import.empty': '请先粘贴要解析的检索式',
    'adv.import.unsupported':
      '该检索式包含嵌套分组 / NEAR 等高级结构，无法完整还原为交互行，已按原文导入（语义保持不变）',
    'fav.save': '收藏',
    'fav.list': '我的收藏',
    'fav.saveTitle': '收藏当前检索式',
    'fav.namePh': '为这条检索式起个名字，如「联邦学习+隐私」',
    'fav.nameRequired': '请填写收藏名称',
    'fav.saved': '已保存',
    'fav.loginRequired': '收藏功能需要先登录（点击右上角登录）',
    'fav.loaded': '已载入到构建器',
    'fav.load': '载入',
    'fav.refresh': '刷新结果数',
    'fav.refreshed': '结果数已更新：{n}',
    'fav.refreshFailed': '结果数查询失败，已保留原有记录',
    'fav.rename': '重命名',
    'fav.renamePrompt': '输入新的名称',
    'fav.delete': '删除',
    'fav.deleteConfirm': '确定删除收藏「{name}」吗？',
    'fav.deleted': '已删除',
    'fav.empty': '还没有收藏的检索式，先收藏一条吧',
    'fav.count': '{n} 篇',
    'fav.countUnknown': '结果数未知',
    'fav.updatedAt': '更新于 {time}',
    'fav.confirm': '确定',
    'fav.cancel': '取消',
    'hero.mcpLink': 'MCP 接入',
    'docs.skillInstall':
      '请下载安装 papervault-search skill 并完成初始化配置\nhttps://papervault.top/downloads/papervault-search-skill.zip',
    'docs.mcpTitle': 'MCP 接入',
    'docs.mcpText':
      '可将 PaperVault 作为 MCP 工具接入 ChatGPT、Claude Desktop 或其他智能体客户端，直接调用 search_papers 检索论文。运行仓库中的 mcp_server.py 并在客户端配置为 stdio server。',
    'auth.login': '登录',
    'auth.loggedIn': '已登录',
    'auth.logout': '退出登录',
    'auth.welcome': '欢迎回来',
    'auth.descriptionLine1': '登录后同步你的搜索偏好',
    'auth.descriptionLine2': '在不同设备间继续使用',
    'auth.chooseMethod': '选择登录方式',
    'auth.loginZhihu': '使用知乎账号登录',
    'auth.loginGithub': '使用 GitHub 登录',
    'auth.footnote': '安全授权 · 不会保存你的第三方密码',
    'auth.close': '关闭',
    'error.network': '后端接口连接异常',
    'error.timeout': '系统接口请求超时',
    'error.http.format': '{code}：{msg}',
    'error.http.400': '错误的请求',
    'error.http.401': '未授权，请重新登录',
    'error.http.403': '拒绝访问',
    'error.http.404': '请求错误，未找到该资源',
    'error.http.405': '请求方法未允许',
    'error.http.408': '请求超时',
    'error.http.500': '服务器端出错',
    'error.http.501': '网络未实现',
    'error.http.502': '网络错误',
    'error.http.503': '服务不可用',
    'error.http.504': '网络超时',
    'error.http.505': 'http版本不支持该请求',
    'error.http.other': '其他连接错误',
    'admin.login': '管理员登录',
    'admin.loginFail': '登录失败',
    'admin.usernamePh': '管理员账号',
    'admin.passwordPh': '管理员密码',
    'admin.overview': '配置概览',
    'admin.logout': '退出',
    'admin.adminLabel': '管理员',
    'admin.labelProvider': 'LLM Provider',
    'admin.labelApiUrl': 'LLM API URL',
    'admin.labelApiKey': 'LLM API Key',
    'admin.labelGithub': 'GitHub',
    'admin.labelZhihu': '知乎',
    'admin.notConfigured': '未配置',
    'admin.configured': '已配置',
    'admin.configuredHidden': '已配置（隐藏）',
    'admin.enabled': '已启用',
    'admin.disabled': '未启用',
    'admin.sep': '：'
  },
  en: {
    'search.mode.label': 'Search mode',
    'search.mode.standard': 'Keyword search',
    'app.title': 'PaperVault',
    'app.slogan': 'Curated top-tier CS papers. Deeply searchable.',
    'search.placeholder':
      'Enter keywords or an expression, e.g. federated AND (privacy OR fairness) AU="Yang Liu" PY=2024-2026',
    'search.placeholder.short': 'Search papers, authors, venues...',
    'search.button': 'Search',
    'search.tab.smart': 'Smart Search',
    'search.tab.advanced': 'Advanced Search',
    'search.heroHint': 'Search with keywords, or ',
    'search.heroHintLink': 'build a field-based query',
    'search.heroHintTail': ' in Advanced Search.',
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
    'toolbar.docs': 'Developer docs',
    'docs.title': 'Search SDK documentation',
    'docs.intro':
      'Connect PaperVault advanced search to scripts, notebooks, and apps with the REST API and query DSL.',
    'docs.quickstart': 'Quick start',
    'docs.quickstartText':
      'Paper search requires no login or API key. Send GET with q, page, and page_size parameters.',
    'docs.dsl': 'Query DSL',
    'docs.dslText':
      'Use TS, TI, AB, AU, SO, and PY fields with AND, OR, NOT, NEAR/x, and quoted phrases.',
    'docs.example': 'cURL example',
    'docs.copy': 'Copy',
    'docs.copied': 'Copied',
    'docs.copyFailed': 'Copy failed; select the text manually',
    'docs.responses': 'Responses and pagination',
    'docs.responsesText':
      'Success responses contain items and meta. page starts at 1 and page_size is capped by the server; errors use error, message, and request_id.',
    'docs.ai': 'Optional AI endpoints',
    'docs.aiText':
      'Use POST /api/v1/suggest for keyword suggestions and POST /api/v1/ai/rerank for relevance reranking.',
    'docs.source': 'Full parameters and examples: ',
    'docs.skillTitle': 'Download the PaperVault Search Skill',
    'docs.skillText':
      'Download and install the PaperVault Search Skill, then complete the initial setup. Unzip the package locally and follow the included guide to connect an MCP-compatible agent client.',
    'docs.skillDownload': 'Download Skill ZIP',
    'docs.skillUrl':
      'Download URL: https://papervault.top/downloads/papervault-search-skill.zip',
    'settings.pageTitle': 'Settings',
    'settings.intro':
      'Configure the LLM provider, API key, and defaults used by AI keyword suggestions and AI result reranking. The API key is kept in this session only (wiped when the browser closes); everything else is stored locally in your browser. Leave fields empty to fall back to the server-side defaults.',
    'settings.aiSuggest.title': 'AI keyword suggestions',
    'settings.aiSuggest.description':
      'These parameters are used by the AI Search button on the home page and the AI suggestion panel in the result list. Picking a provider auto-fills the default endpoint, model, and protocol; you can still edit them, or leave everything empty to use the server defaults.',
    'settings.aiSuggest.form.provider': 'Provider',
    'settings.aiSuggest.form.baseUrl': 'Endpoint URL',
    'settings.aiSuggest.form.model': 'Model',
    'settings.aiSuggest.form.apiKey': 'API key',
    'settings.aiSuggest.form.apiKeyPh': 'Leave empty to use server env var',
    'settings.aiSuggest.form.protocol': 'Protocol (auto)',
    'settings.aiSuggest.form.temperature': 'Temperature',
    'settings.aiSuggest.form.maxKeywords': 'Keyword count',
    'settings.aiSuggest.form.maxTokens': 'Max output tokens',
    'settings.aiSuggest.action.save': 'Save',
    'settings.aiSuggest.action.clear': 'Clear',
    'settings.aiSuggest.saved': '✓ Saved',
    'settings.aiSuggest.hint.apiKey':
      'Key lives only in this session (wiped on browser close) and is never sent to our servers.',
    'settings.aiSuggest.hint.provider':
      'Picking a provider auto-fills the default endpoint, model, and protocol; you can still edit them.',
    'settings.aiSuggest.test.title': 'Test connection',
    'settings.aiSuggest.test.description':
      'Enter a research description and click Generate to verify that the current settings can reach the LLM.',
    'settings.aiSuggest.test.queryPh': 'Enter a description…',
    'settings.aiSuggest.test.button': 'Generate',
    'settings.aiSuggest.test.empty': 'No keywords returned',
    'settings.aiSuggest.test.errorQuery': 'Please enter a query to test',
    'settings.aiSuggest.test.errorProvider': 'Please pick a provider first',
    'settings.aiSuggest.test.errorUnknown': 'Request failed',
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
    'guess.header': 'AI keyword suggestions',
    'guess.provider': 'from {provider} · {ms}ms',
    'guess.empty': 'No suggestion yet',
    'guess.replace': 'Search this only',
    'guess.merge': 'Add to query (OR)',
    'search.aiSearch.button': 'AI search',
    'search.aiSearch.dialogTitle': 'AI search (keywords + rerank)',
    'search.aiSearch.seedPh': 'Describe your topic, e.g. "time series llm"',
    'search.aiSearch.run': 'Generate',
    'search.aiSearch.rerank': 'Rerank results by AI relevance',
    'search.aiSearch.empty': 'No suggestions',
    'search.aiSearch.toastFail': 'AI search failed: {msg}',
    'search.aiSearch.toastFailGeneric': 'Request failed',
    'search.aiSearch.toastNoSeed': 'Please enter a topic first',
    'search.aiSearch.toastNoKey': 'Configure your API key in Settings first',
    'search.aiSearch.goSettings': 'Open Settings',
    'search.aiSearch.hint':
      'AI expands your keywords automatically. Plain search keeps working unchanged.',
    'search.aiSearch.mergeCapHint':
      'Only the first {cap} keywords will be merged to avoid overshooting the result cap',
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
    'adv.cheatsheet.confs': 'Indexed venues (usable in SO=)',
    'adv.copy': 'Copy',
    'adv.copyOk': 'Expression copied',
    'adv.copyFail': 'Copy failed — please select the text manually',
    'adv.import.label': 'Import expression from text',
    'adv.import.placeholder':
      'Paste a text query, e.g. TS=(federated OR privacy) AU="Yang Liu" PY=2024-2026',
    'adv.import.apply': 'Parse into builder',
    'adv.import.empty': 'Paste an expression to parse first',
    'adv.import.unsupported':
      'This expression uses nested groups / NEAR and cannot be fully expanded into rows; imported as raw text (semantics preserved)',
    'fav.save': 'Save',
    'fav.list': 'Saved queries',
    'fav.saveTitle': 'Save current expression',
    'fav.namePh': 'Name this query, e.g. "federated + privacy"',
    'fav.nameRequired': 'Please enter a name',
    'fav.saved': 'Saved',
    'fav.loginRequired': 'Sign in (top-right corner) to use saved queries',
    'fav.loaded': 'Loaded into the builder',
    'fav.load': 'Load',
    'fav.refresh': 'Refresh count',
    'fav.refreshed': 'Result count updated: {n}',
    'fav.refreshFailed':
      'Failed to fetch the result count; kept the previous value',
    'fav.rename': 'Rename',
    'fav.renamePrompt': 'Enter a new name',
    'fav.delete': 'Delete',
    'fav.deleteConfirm': 'Delete saved query "{name}"?',
    'fav.deleted': 'Deleted',
    'fav.empty': 'No saved queries yet — save one first',
    'fav.count': '{n} papers',
    'fav.countUnknown': 'count unknown',
    'fav.updatedAt': 'Updated {time}',
    'fav.confirm': 'OK',
    'fav.cancel': 'Cancel',
    'hero.mcpLink': 'MCP integration',
    'docs.skillInstall':
      'Download and install the papervault-search skill, then finish the initial setup\nhttps://papervault.top/downloads/papervault-search-skill.zip',
    'docs.mcpTitle': 'MCP integration',
    'docs.mcpText':
      'PaperVault can be plugged into ChatGPT, Claude Desktop, or any other MCP-capable agent client as an MCP tool exposing the search_papers command. Run mcp_server.py from the repository and register it as a stdio server in your client.',
    'auth.login': 'Sign in',
    'auth.loggedIn': 'Signed in',
    'auth.logout': 'Sign out',
    'auth.welcome': 'Welcome back',
    'auth.descriptionLine1': 'Sync your search preferences',
    'auth.descriptionLine2': 'and continue on any device.',
    'auth.chooseMethod': 'Choose a sign-in method',
    'auth.loginZhihu': 'Continue with Zhihu',
    'auth.loginGithub': 'Continue with GitHub',
    'auth.footnote': 'Secure OAuth · we never store your third-party password',
    'auth.close': 'Close',
    'error.network': 'Cannot reach the backend API',
    'error.timeout': 'The API request timed out',
    'error.http.format': '{code}: {msg}',
    'error.http.400': 'Bad request',
    'error.http.401': 'Unauthorized, please sign in again',
    'error.http.403': 'Access denied',
    'error.http.404': 'Resource not found',
    'error.http.405': 'Method not allowed',
    'error.http.408': 'Request timeout',
    'error.http.500': 'Internal server error',
    'error.http.501': 'Not implemented',
    'error.http.502': 'Bad gateway',
    'error.http.503': 'Service unavailable',
    'error.http.504': 'Gateway timeout',
    'error.http.505': 'HTTP version not supported',
    'error.http.other': 'Other connection error',
    'admin.login': 'Admin sign in',
    'admin.loginFail': 'Sign-in failed',
    'admin.usernamePh': 'Admin username',
    'admin.passwordPh': 'Admin password',
    'admin.overview': 'Configuration overview',
    'admin.logout': 'Sign out',
    'admin.adminLabel': 'Admin',
    'admin.labelProvider': 'LLM Provider',
    'admin.labelApiUrl': 'LLM API URL',
    'admin.labelApiKey': 'LLM API Key',
    'admin.labelGithub': 'GitHub',
    'admin.labelZhihu': 'Zhihu',
    'admin.notConfigured': 'Not configured',
    'admin.configured': 'Configured',
    'admin.configuredHidden': 'Configured (hidden)',
    'admin.enabled': 'Enabled',
    'admin.disabled': 'Disabled',
    'admin.sep': ': '
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
