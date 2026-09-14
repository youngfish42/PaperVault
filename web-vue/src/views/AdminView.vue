<script setup lang="ts">
import { ref, onMounted } from 'vue'
import request from '@/utils/axios'
import { useI18n } from '@/utils/i18n'

const { t } = useI18n()

const username = ref('')
const password = ref('')
const loggedIn = ref(false)
const config = ref<any>(null)
const error = ref('')
async function load() {
  const me: any = await request({ url: '/v1/auth/me' })
  loggedIn.value = me.authenticated
  if (loggedIn.value) config.value = await request({ url: '/v1/admin/config' })
}
async function login() {
  try {
    await request({
      url: '/v1/auth/login',
      method: 'POST',
      data: { username: username.value, password: password.value }
    })
    await load()
  } catch {
    error.value = t('admin.loginFail')
  }
}
async function logout() {
  await request({ url: '/v1/auth/logout', method: 'POST' })
  loggedIn.value = false
  config.value = null
}
onMounted(() => load().catch(() => {}))
</script>
<template>
  <main class="admin-page">
    <h1>PaperVault Admin</h1>
    <section v-if="!loggedIn" class="card">
      <el-input
        v-model="username"
        :placeholder="t('admin.usernamePh')"
      /><el-input
        v-model="password"
        type="password"
        show-password
        :placeholder="t('admin.passwordPh')"
        @keyup.enter="login"
      /><el-button type="primary" @click="login">{{
        t('admin.login')
      }}</el-button>
      <p v-if="error" class="error">{{ error }}</p>
    </section>
    <section v-else class="card">
      <header>
        <h2>{{ t('admin.overview') }}</h2>
        <el-button @click="logout">{{ t('admin.logout') }}</el-button>
      </header>
      <p>
        {{ t('admin.adminLabel') }}{{ t('admin.sep')
        }}{{ config?.adminUsername }}
      </p>
      <p>
        {{ t('admin.labelProvider') }}{{ t('admin.sep')
        }}{{ config?.llm?.provider || t('admin.notConfigured') }}
      </p>
      <p>
        {{ t('admin.labelApiUrl') }}{{ t('admin.sep')
        }}{{
          config?.llm?.baseUrlConfigured
            ? t('admin.configured')
            : t('admin.notConfigured')
        }}
      </p>
      <p>
        {{ t('admin.labelApiKey') }}{{ t('admin.sep')
        }}{{
          config?.llm?.apiKeyConfigured
            ? t('admin.configuredHidden')
            : t('admin.notConfigured')
        }}
      </p>
      <p>
        {{ t('admin.labelGithub') }}{{ t('admin.sep')
        }}{{
          config?.oauth?.github ? t('admin.enabled') : t('admin.disabled')
        }}
        / {{ t('admin.labelZhihu') }}{{ t('admin.sep')
        }}{{ config?.oauth?.zhihu ? t('admin.enabled') : t('admin.disabled') }}
      </p>
    </section>
  </main>
</template>
<style scoped>
.admin-page {
  max-width: var(--pv-page-width);
  margin: 60px auto;
  padding: 24px;
}
.card {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 24px;
  border: 1px solid var(--el-border-color);
  border-radius: var(--pv-card-radius);
}
header {
  display: flex;
  justify-content: space-between;
}
.oauth {
  display: flex;
  gap: 16px;
}
.error {
  color: #d03050;
}
</style>
