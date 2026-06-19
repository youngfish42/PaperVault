<!--
 * @Author: 0x3E5
 * @Date: 2023-02-11 19:27:09
 * @LastEditTime: 2023-02-21 15:44:38
 * @LastEditors: 0x3E5
 * @Description: 
 * @FilePath: \ai-paper-search-web\src\components\AdvancedSettingDlg.vue
-->
<script lang="ts" setup>
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from '@/utils/i18n'

const { t } = useI18n()

type FORMDATA = {
  query: string
  searchtype: string
  year: string
  sp_year: string
  sp_author: string
  confs: string[]
}

const props = defineProps<{
  data: FORMDATA
  confs?: string[]
}>()
const emits = defineEmits<{
  (e: 'update:data', value: FORMDATA): void
}>()

const isVisible = ref(false)
const CURRENT_YEAR = new Date().getFullYear()
const SPECIFIC_YEAR_LIST = computed(() => [
  {
    label: t('year.since', { year: CURRENT_YEAR }),
    value: `${String(CURRENT_YEAR)}`
  },
  {
    label: t('year.since', { year: CURRENT_YEAR - 3 }),
    value: `${String(CURRENT_YEAR - 3)}`
  },
  {
    label: t('year.since', { year: CURRENT_YEAR - 5 }),
    value: `${String(CURRENT_YEAR - 5)}`
  },
  { label: t('year.all'), value: '' }
])

const confsList = computed(() => props.confs ?? [])

const formData: FORMDATA = reactive({
  query: '',
  searchtype: '',
  year: '',
  sp_year: '',
  sp_author: '',
  confs: []
})

watch(
  () => props.data,
  v => {
    if (v) {
      Object.assign(formData, v)
    }
  },
  {
    deep: true,
    immediate: true
  }
)

const checkMethod: (method: string) => void = method => {
  const all = confsList.value
  if (method === 'all') {
    formData.confs = [...all]
  } else if (method === 'invert') {
    const SELECTED = new Set(formData.confs)
    formData.confs = all.filter(v => !SELECTED.has(v))
  }
}

const resetForm = (): void => {
  formData.year = ''
  formData.sp_year = ''
  formData.sp_author = ''
  formData.confs = [...confsList.value]
}

const confirmForm = (): void => {
  emits('update:data', { ...formData })
  isVisible.value = false
}

defineExpose({
  isVisible
})
</script>
<template>
  <el-dialog
    class="dialog-advancedSetting"
    v-model="isVisible"
    :title="t('dlg.title')"
  >
    <el-form :model="formData" label-width="120px">
      <el-form-item :label="t('dlg.years')" prop="year">
        <el-select class="w-100" v-model="formData.year">
          <el-option
            v-for="(itm, index) in SPECIFIC_YEAR_LIST"
            :key="index"
            :label="itm.label"
            :value="itm.value"
          >
          </el-option>
        </el-select>
      </el-form-item>
      <el-form-item :label="t('dlg.specificYear')" prop="sp_year">
        <el-input
          v-model="formData.sp_year"
          :placeholder="t('dlg.specificYearPh')"
          clearable
        />
      </el-form-item>
      <el-form-item :label="t('dlg.specificAuthor')" prop="sp_author">
        <el-input
          v-model="formData.sp_author"
          :placeholder="t('dlg.specificAuthorPh')"
          clearable
        />
      </el-form-item>
      <el-form-item :label="t('dlg.confs')" prop="confs">
        <el-row class="w-100" :gutter="20">
          <el-col :span="12" :offset="0">
            <el-link type="primary" @click="checkMethod('all')">{{
              t('dlg.checkAll')
            }}</el-link>
          </el-col>
          <el-col :span="12" :offset="0">
            <el-link type="primary" @click="checkMethod('invert')">{{
              t('dlg.checkInvert')
            }}</el-link>
          </el-col>
        </el-row>

        <el-checkbox-group v-model="formData.confs">
          <el-row>
            <el-col
              :xs="12"
              :sm="12"
              :md="8"
              :lg="8"
              :xl="6"
              v-for="(itm, index) in confsList"
              :key="index"
            >
              <el-checkbox :value="itm" :label="itm">
                {{ itm }}
              </el-checkbox>
            </el-col>
          </el-row>
        </el-checkbox-group>
      </el-form-item>
    </el-form>
    <template #footer>
      <span class="dialog-footer">
        <el-button @click="resetForm">{{ t('dlg.reset') }}</el-button>
        <el-button type="primary" @click="confirmForm">{{
          t('dlg.done')
        }}</el-button>
      </span>
    </template>
  </el-dialog>
</template>

<style>
@media (min-width: 1920px) {
  .dialog-advancedSetting {
    width: 45%;
  }
}
@media (max-width: 1920px) {
  .dialog-advancedSetting {
    width: 50%;
  }
}
@media (max-width: 1200px) {
  .dialog-advancedSetting {
    width: 65%;
  }
}
@media (max-width: 992px) {
  .dialog-advancedSetting {
    width: 75%;
  }
}
@media (max-width: 768px) {
  .dialog-advancedSetting {
    width: 92%;
  }
}
</style>
<style scoped>
.checkbox-advancedSetting {
  width: 50%;
  margin-right: 0%;
}
</style>
