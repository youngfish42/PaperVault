<script setup lang="ts">
import { ref, onMounted } from 'vue'
import request from '@/utils/axios'
const username=ref(''); const password=ref(''); const loggedIn=ref(false); const config=ref<any>(null); const error=ref('')
async function load(){const me:any=await request({url:'/v1/auth/me'}); loggedIn.value=me.authenticated; if(loggedIn.value) config.value=await request({url:'/v1/admin/config'})}
async function login(){try{await request({url:'/v1/auth/login',method:'POST',data:{username:username.value,password:password.value}});await load()}catch{error.value='登录失败'}}
async function logout(){await request({url:'/v1/auth/logout',method:'POST'});loggedIn.value=false;config.value=null} onMounted(()=>load().catch(()=>{}))
</script>
<template><main class="admin-page"><h1>PaperVault Admin</h1><section v-if="!loggedIn" class="card"><el-input v-model="username" placeholder="管理员账号"/><el-input v-model="password" type="password" show-password placeholder="管理员密码" @keyup.enter="login"/><el-button type="primary" @click="login">管理员登录</el-button><p v-if="error" class="error">{{error}}</p></section><section v-else class="card"><header><h2>配置概览</h2><el-button @click="logout">退出</el-button></header><p>管理员：{{config?.adminUsername}}</p><p>LLM Provider：{{config?.llm?.provider||'未配置'}}</p><p>LLM API URL：{{config?.llm?.baseUrlConfigured?'已配置':'未配置'}}</p><p>LLM API Key：{{config?.llm?.apiKeyConfigured?'已配置（隐藏）':'未配置'}}</p><p>GitHub：{{config?.oauth?.github?'已启用':'未启用'}}　知乎：{{config?.oauth?.zhihu?'已启用':'未启用'}}</p></section></main></template>
<style scoped>.admin-page{max-width:var(--pv-page-width);margin:60px auto;padding:24px}.card{display:flex;flex-direction:column;gap:14px;padding:24px;border:1px solid var(--el-border-color);border-radius:var(--pv-card-radius)}header{display:flex;justify-content:space-between}.oauth{display:flex;gap:16px}.error{color:#d03050}</style>
