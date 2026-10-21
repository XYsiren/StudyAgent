<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { ArrowUpRight, Pause, Play, RotateCcw, Timer } from 'lucide-vue-next'
const minutes=ref(25),seconds=ref(25*60),running=ref(false),finished=ref(false)
let interval:ReturnType<typeof setInterval>|undefined,deadline=0
const label=computed(()=>`${String(Math.floor(seconds.value/60)).padStart(2,'0')}:${String(seconds.value%60).padStart(2,'0')}`)
const progress=computed(()=>1-seconds.value/(minutes.value*60))
function pause(){clearInterval(interval);interval=undefined;running.value=false}
function tick(){seconds.value=Math.max(0,Math.ceil((deadline-Date.now())/1000));if(seconds.value===0){pause();finished.value=true}}
function toggle(){if(running.value){tick();pause();return}if(seconds.value===0)reset();deadline=Date.now()+seconds.value*1000;running.value=true;finished.value=false;interval=setInterval(tick,250)}
function reset(){pause();seconds.value=minutes.value*60;finished.value=false}
function preset(value:number){minutes.value=value;reset()}
onUnmounted(pause)
</script>
<template>
  <article class="focus-card">
    <header><span><Timer :size="15"/> 一小段专注</span><span class="focus-live">{{ running?'进行中':'留给自己' }}</span></header>
    <div class="focus-dial"><svg viewBox="0 0 180 180" aria-hidden="true"><circle cx="90" cy="90" r="76" class="focus-track"/><circle cx="90" cy="90" r="76" class="focus-fill" :stroke-dasharray="`${progress*477.52} 477.52`"/></svg><div><strong :aria-label="`剩余 ${label}`">{{ label }}</strong><small>{{ finished?'这一刻，值得一个小小的肯定。':running?'只做眼前这一件事。':'把注意力，放回此刻。' }}</small></div></div>
    <div class="focus-presets" aria-label="专注时长"><button v-for="value in [15,25,45]" :key="value" :class="{active:minutes===value}" :aria-pressed="minutes===value" @click="preset(value)">{{ value }} 分钟</button></div>
    <div class="focus-controls"><button class="focus-start" @click="toggle"><Pause v-if="running" :size="15"/><Play v-else :size="15"/>{{ running?'暂停一下':seconds===minutes*60||finished?'开始专注':'继续专注' }}<ArrowUpRight :size="16"/></button><button class="focus-reset" aria-label="重置专注计时" @click="reset"><RotateCcw :size="16"/></button></div>
    <p v-if="finished" class="focus-complete" role="status">本次计时已结束，可以休息一下了。</p>
    <p v-else class="focus-footnote">计时仅在本页运行，学习任务需手动标记完成。</p>
  </article>
</template>
<style scoped>
.focus-card{background:#293b32;color:#faf7ed;border-radius:22px;padding:22px;position:relative;overflow:hidden}.focus-card:before{content:'';position:absolute;right:-65px;top:-70px;width:180px;height:180px;border:1px solid #f7f5eb12;border-radius:50%;pointer-events:none}.focus-card header{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:13px}.focus-card header>span:first-child{display:flex;gap:8px;align-items:center}.focus-live{font-size:10px;padding:4px 7px;border:1px solid #f7f5eb26;border-radius:20px;color:#ccd6bc}.focus-dial{position:relative;max-width:215px;margin:14px auto}.focus-dial svg{width:100%;transform:rotate(-90deg)}.focus-track,.focus-fill{fill:none;stroke-width:2}.focus-track{stroke:#ffffff16}.focus-fill{stroke:#d9e69a;stroke-linecap:round;transition:stroke-dasharray .4s linear}.focus-dial>div{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:9px}.focus-dial strong{font-family:Georgia,'Times New Roman',serif;font-size:48px;font-weight:400;letter-spacing:-2px;font-variant-numeric:tabular-nums}.focus-dial small{font-size:11px;color:#c5ceb9}.focus-presets{display:flex;justify-content:center;gap:7px;margin-bottom:20px}.focus-presets button{color:#c5ceb9;border-radius:7px;font-size:11px;padding:6px 9px}.focus-presets button.active{background:#e1ebad;color:#283b32}.focus-controls{display:flex;gap:8px}.focus-start{display:flex;align-items:center;justify-content:center;gap:8px;flex:1;padding:12px 10px;background:#e1ebad;color:#25382d;border-radius:10px;font-size:13px;font-weight:600}.focus-start>svg:last-child{margin-left:auto}.focus-reset{display:flex;align-items:center;justify-content:center;width:42px;border:1px solid #ffffff28;border-radius:10px;color:#f5f4e9}.focus-complete,.focus-footnote{font-size:11px!important;line-height:1.7;text-align:center;margin-top:13px!important;color:#c5ceb9!important}.focus-complete{color:#e1ebad!important}@media(prefers-reduced-motion:reduce){.focus-fill{transition:none}}
</style>
