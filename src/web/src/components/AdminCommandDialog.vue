<script setup lang="ts">
import { reactive, ref, watch } from 'vue'

const props = defineProps<{
  modelValue: boolean
  title: string
  description: string
  confirmText?: string
  loading?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submit: [value: { reason: string; adminToken: string; idempotencyKey: string }]
}>()

const formRef = ref()
const form = reactive({ reason: '', adminToken: '' })
let idempotencyKey = ''

watch(
  () => props.modelValue,
  (visible) => {
    if (visible) {
      form.reason = ''
      idempotencyKey = crypto.randomUUID()
    }
  },
)

async function submit() {
  await formRef.value?.validate()
  emit('submit', {
    reason: form.reason.trim(),
    adminToken: form.adminToken,
    idempotencyKey,
  })
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="title"
    width="520px"
    :close-on-click-modal="false"
    :close-on-press-escape="!loading"
    :show-close="!loading"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <el-alert :title="description" type="warning" :closable="false" show-icon />
    <el-form ref="formRef" :model="form" label-position="top" class="command-form">
      <el-form-item
        label="操作原因"
        prop="reason"
        :rules="[
          { required: true, message: '请输入操作原因', trigger: 'blur' },
          { min: 3, max: 500, message: '原因长度为 3 到 500 个字符', trigger: 'blur' },
        ]"
      >
        <el-input
          v-model="form.reason"
          type="textarea"
          :rows="3"
          maxlength="500"
          show-word-limit
          placeholder="说明人工介入的依据"
        />
      </el-form-item>
      <el-form-item
        label="管理 Token"
        prop="adminToken"
        :rules="[{ required: true, message: '请输入管理 Token', trigger: 'blur' }]"
      >
        <el-input
          v-model="form.adminToken"
          type="password"
          show-password
          autocomplete="off"
          placeholder="ADMIN_API_TOKEN"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button :disabled="loading" @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="loading" @click="submit">
        {{ confirmText ?? '确认执行' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.command-form {
  margin-top: 20px;
}
</style>
