<script setup lang="ts">
import { ElMessage, type FormInstance } from 'element-plus'
import { reactive, ref, watch } from 'vue'

import { validateForm } from '@/utils/form'
import { createIdempotencyKey } from '@/utils/idempotency'

const props = defineProps<{
  modelValue: boolean
  title: string
  description: string
  confirmText?: string
  loading?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submit: [value: { reason: string; idempotencyKey: string }]
}>()

const formRef = ref<FormInstance>()
const form = reactive({ reason: '' })
let idempotencyKey = ''

watch(
  () => props.modelValue,
  (visible) => {
    if (visible) {
      form.reason = ''
      idempotencyKey = createIdempotencyKey()
    }
  },
)

async function submit() {
  if (!(await validateForm(formRef.value))) {
    ElMessage.warning('请完整填写表单后再提交')
    return
  }
  emit('submit', {
    reason: form.reason.trim(),
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
          { max: 500, message: '原因最多 500 个字符', trigger: 'blur' },
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
