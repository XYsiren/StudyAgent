<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
import { api, API_BASE, downloadDocument } from '../../api'
import type { Slide, Source } from './types'
defineProps<{sources: Source[]}>()
const slide = ref<(Slide & {document_name: string}) | null>(null)
const error = ref('')
const pdf = ref<{url:string;name:string;page:number}|null>(null)
function closePdf(){if(pdf.value)URL.revokeObjectURL(pdf.value.url);pdf.value=null}
onUnmounted(closePdf)
async function open(source: Source) {
  error.value = ''
  try {
    if (source.slide_number) slide.value = await api(`/materials/${source.document_id}/slides/${source.slide_number}${source.revision_id?`?revision_id=${encodeURIComponent(source.revision_id)}`:""}`)
    else if(source.document_name.toLowerCase().endsWith('.pdf')) {
      const endpoint=source.revision_id?`/documents/${source.document_id}/versions/${source.revision_id}/download`:`/documents/${source.document_id}/download`
      const response=await fetch(API_BASE+endpoint,{credentials:'same-origin'})
      if(!response.ok)throw new Error(`原文读取失败（${response.status}）`)
      const data=await response.blob();closePdf()
      pdf.value={url:URL.createObjectURL(new Blob([data],{type:'application/pdf'})),name:source.document_name,page:source.page||1}
    } else await downloadDocument(source.document_id, source.document_name, source.revision_id)
  } catch (e) { error.value = e instanceof Error ? e.message : '打开来源失败' }
}
</script>
<template>
  <div class="learning-sources">
    <details v-for="(source,index) in sources" :key="source.chunk_id || source.id || index">
      <summary>[{{ index+1 }}] {{ source.document_name }} · {{ source.page_label || (source.page ? `第 ${source.page} 页` : '原文片段') }}</summary>
      <p class="source-text">{{ source.text }}</p><button class="btn secondary" @click="open(source)">{{ source.slide_number ? '查看幻灯片来源' : source.document_name.toLowerCase().endsWith('.pdf') ? '打开原文指定页' : '下载原文' }}</button>
    </details>
    <p v-if="error" role="alert">{{ error }}</p>
    <div v-if="pdf" class="modal-backdrop" @click.self="closePdf"><section class="modal learning-pdf" role="dialog" aria-modal="true" aria-label="PDF 原文来源"><div><h3>{{ pdf.name }} · 第 {{ pdf.page }} 页</h3><button class="btn secondary" @click="closePdf">关闭</button></div><iframe :src="`${pdf.url}#page=${pdf.page}`" title="PDF 原文指定页" style="width:100%;height:65vh;border:0"/></section></div>
    <div v-if="slide" class="modal-backdrop" @click.self="slide=null"><section class="modal learning-slide" role="dialog" aria-modal="true" aria-label="幻灯片来源"><button class="btn secondary" @click="slide=null">关闭</button><p>{{ slide.document_name }} · 第 {{ slide.slide_number }} 页幻灯片</p><h2>{{ slide.slide_title || '幻灯片来源' }}</h2><img v-if="slide.thumbnail" :src="slide.thumbnail" alt="待视觉解析图片预览"/><p class="source-text">{{ slide.text }}</p><small>页面类型：{{ slide.page_type }} · 未解析对象：{{ slide.unparsed_objects }}</small></section></div>
  </div>
</template>

<style scoped>
.learning-pdf{width:min(1000px,94vw);max-width:1000px}.learning-pdf>div{display:flex;justify-content:space-between;align-items:center;gap:12px}.learning-pdf h3{font-size:15px}
</style>
