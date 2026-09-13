<script setup lang="ts">
import { useDark, useToggle } from '@vueuse/core'
import { ElMessage } from 'element-plus'
import MainNavBar from '@/components/MainNavBar.vue'
import { useI18n } from '@/utils/i18n'
const isDark = useDark()
const toggleDark = useToggle(isDark)
const { t } = useI18n()
const curlExample = `curl -G 'https://papervault.top/api/v1/papers' \\\n  --data-urlencode 'q=TS=(federated AND privacy) SO=ICLR PY=2024-2026' \\\n  --data-urlencode 'page=1' --data-urlencode 'page_size=20'`
const copy = async (value: string): Promise<void> => {
  try {
    await navigator.clipboard.writeText(value)
    ElMessage.success(t('docs.copied'))
  } catch {
    ElMessage.warning(t('docs.copyFailed'))
  }
}
</script>
<template>
  <main class="pv-docs-page">
    <MainNavBar
      active-key="docs"
      :is-dark="isDark"
      @toggle-dark="toggleDark()"
    />
    <section class="pv-container pv-docs-body">
      <header class="pv-docs-hero">
        <p class="pv-docs-eyebrow">PaperVault API</p>
        <h1>{{ t('docs.title') }}</h1>
        <p>{{ t('docs.intro') }}</p>
      </header>
      <div class="pv-docs-grid">
        <article class="pv-docs-card">
          <h2>{{ t('docs.quickstart') }}</h2>
          <p>{{ t('docs.quickstartText') }}</p>
          <div class="pv-code"><code>GET /api/v1/papers</code></div>
        </article>
        <article class="pv-docs-card">
          <h2>{{ t('docs.dsl') }}</h2>
          <p>{{ t('docs.dslText') }}</p>
          <div class="pv-docs-tags">
            <code>TS</code><code>TI</code><code>AB</code><code>AU</code
            ><code>SO</code><code>PY</code><code>AND</code><code>OR</code
            ><code>NOT</code><code>NEAR/x</code>
          </div>
        </article>
        <article class="pv-docs-card pv-docs-card--wide">
          <div class="pv-docs-card-head">
            <h2>{{ t('docs.example') }}</h2>
            <el-button size="small" @click="copy(curlExample)">{{
              t('docs.copy')
            }}</el-button>
          </div>
          <pre><code>{{ curlExample }}</code></pre>
        </article>
        <article class="pv-docs-card">
          <h2>{{ t('docs.responses') }}</h2>
          <p>{{ t('docs.responsesText') }}</p>
          <div class="pv-code">
            <code>{ items, meta: { page, page_size, total } }</code>
          </div>
        </article>
        <article class="pv-docs-card">
          <h2>{{ t('docs.ai') }}</h2>
          <p>{{ t('docs.aiText') }}</p>
          <div class="pv-code">
            <code>POST /api/v1/suggest<br />POST /api/v1/ai/rerank</code>
          </div>
        </article>
        <article class="pv-docs-card pv-docs-card--wide">
          <h2>MCP 接入 / MCP integration</h2>
          <p>可将 PaperVault 作为 MCP 工具接入 ChatGPT、Claude Desktop 或其他智能体客户端，直接调用 <code>search_papers</code> 检索论文。运行仓库中的 <code>mcp_server.py</code> 并在客户端配置为 stdio server。</p>
          <div class="pv-code"><code>python /path/to/PaperVault/mcp_server.py</code></div>
        </article>
      </div>
      <p class="pv-docs-foot">
        {{ t('docs.source') }}
        <a
          href="https://github.com/youngfish42/PaperVault/blob/main/docs/search-sdk.md"
          target="_blank"
          rel="noopener"
          >docs/search-sdk.md ↗</a
        >
      </p>
    </section>
  </main>
</template>
<style scoped>
.pv-docs-body {
  max-width: var(--pv-page-width);
  padding: 42px 24px 64px;
}
.pv-docs-hero {
  max-width: 720px;
  margin-bottom: 28px;
}
.pv-docs-eyebrow {
  color: var(--el-color-primary);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  margin: 0 0 10px;
}
.pv-docs-hero h1 {
  font-size: 34px;
  margin: 0 0 12px;
  color: var(--el-text-color-primary);
}
.pv-docs-hero p:last-child {
  color: var(--el-text-color-secondary);
  line-height: 1.7;
  margin: 0;
}
.pv-docs-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
.pv-docs-card {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 12px;
  padding: 22px;
  background: var(--el-bg-color);
  box-shadow: 0 4px 16px rgba(20, 30, 60, 0.04);
}
.pv-docs-card--wide {
  grid-column: 1/-1;
}
.pv-docs-card h2 {
  font-size: 18px;
  margin: 0 0 10px;
  color: var(--el-text-color-primary);
}
.pv-docs-card p {
  font-size: 14px;
  line-height: 1.7;
  color: var(--el-text-color-secondary);
  margin: 0 0 14px;
}
.pv-docs-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.pv-code,
.pv-docs-card pre {
  background: var(--el-fill-color-light);
  border-radius: 8px;
  padding: 12px;
  overflow: auto;
  color: var(--el-text-color-primary);
  font-size: 13px;
  line-height: 1.6;
}
.pv-docs-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.pv-docs-tags code {
  background: var(--el-fill-color-light);
  padding: 5px 8px;
  border-radius: 5px;
  font-size: 12px;
}
.pv-docs-foot {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin-top: 24px;
}
.pv-docs-foot a {
  color: var(--el-color-primary);
}
@media (max-width: 700px) {
  .pv-docs-grid {
    grid-template-columns: 1fr;
  }
  .pv-docs-card--wide {
    grid-column: auto;
  }
  .pv-docs-hero h1 {
    font-size: 28px;
  }
}
</style>
