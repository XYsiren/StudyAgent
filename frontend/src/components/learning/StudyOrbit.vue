<script setup lang="ts">
import { computed } from 'vue'
import { ArrowUpRight, Sparkles } from 'lucide-vue-next'
import type { Point } from './types'
const props=defineProps<{points:Point[]}>()
defineEmits<{open:[]}>()
const confirmed=computed(()=>props.points.filter(p=>p.confirmed_at))
const mastered=computed(()=>confirmed.value.filter(p=>p.mastery_state==='mastered').length)
const shown=computed(()=>confirmed.value.slice(0,12))
function position(index:number){const angle=(index/Math.max(shown.value.length,1))*Math.PI*2-Math.PI/2;return {x:140+Math.cos(angle)*94,y:122+Math.sin(angle)*85}}
</script>
<template>
  <article class="orbit-card"><header><span><Sparkles :size="15"/> 知识的连接</span><button aria-label="查看知识点大纲" @click="$emit('open')"><ArrowUpRight :size="18"/></button></header>
    <svg viewBox="0 0 280 244" role="img" :aria-label="`已确认 ${confirmed.length} 个知识点，已掌握 ${mastered} 个`"><ellipse cx="140" cy="122" rx="94" ry="85" fill="none" stroke="currentColor" stroke-dasharray="3 6" opacity=".22"/><ellipse cx="140" cy="122" rx="58" ry="52" fill="none" stroke="currentColor" opacity=".1"/>
      <template v-for="(point,index) in shown" :key="point.id"><line x1="140" y1="122" :x2="position(index).x" :y2="position(index).y" stroke="currentColor" opacity=".14"/><circle :cx="position(index).x" :cy="position(index).y" :r="point.mastery_state==='mastered'?7:5" :fill="point.mastery_state==='mastered'?'#58754a':point.mastery_state==='learning'?'#cb7956':'#bcb5a2'"><title>{{ point.title }} · {{ {mastered:'已掌握',learning:'学习中',unknown:'尚未标记'}[point.mastery_state] }}</title></circle></template>
      <circle cx="140" cy="122" r="32" fill="#eeeddf"/><text x="140" y="123" text-anchor="middle" dominant-baseline="middle" class="orbit-number">{{ mastered }}<tspan class="orbit-total"> / {{ confirmed.length }}</tspan></text><text x="140" y="173" text-anchor="middle" class="orbit-caption">已掌握 / 已确认</text>
    </svg><p v-if="!confirmed.length">确认第一个知识点，让知识慢慢连成一片。</p><p v-else>每一个小点，都是你正在理解的世界。<small v-if="confirmed.length>12">图中展示前 12 个知识点，计数包含全部。</small></p>
    <div class="orbit-legend"><span><i/>未标记</span><span><i/>学习中</span><span><i/>已掌握</span></div>
  </article>
</template>
<style scoped>
.orbit-card{border:1px solid #e2ddce;border-radius:22px;background:#faf8ef;padding:21px;color:#4f5a41}.orbit-card header{display:flex;align-items:center;justify-content:space-between;font-size:13px;font-weight:600}.orbit-card header>span{display:flex;align-items:center;gap:8px}.orbit-card header>button{display:flex;color:#6c775c}.orbit-card>svg{width:100%;max-height:245px;margin-top:4px}.orbit-number{fill:#3d5037;font-family:Georgia,serif;font-size:24px}.orbit-total{font-size:14px;fill:#8a8d78}.orbit-caption{font-size:10px;fill:#8a8d78;letter-spacing:1px}.orbit-card>p{font-size:12px;line-height:1.8;color:#71745f;text-align:center}.orbit-card p small{display:block;font-size:10px}.orbit-legend{display:flex;justify-content:center;gap:14px;font-size:10px;color:#858776;margin-top:17px}.orbit-legend span{display:flex;gap:5px;align-items:center}.orbit-legend i{height:5px;width:5px;border-radius:50%;background:#bcb5a2}.orbit-legend span:nth-child(2) i{background:#cb7956}.orbit-legend span:nth-child(3) i{background:#58754a}
</style>
