// Exercise the user-facing player, not just the server port. No remote requests.
import fs from 'node:fs'; import path from 'node:path'; import {createRequire} from 'node:module'; import {createHash} from 'node:crypto';
const [projectArg,urlArg,browserPath,output]=process.argv.slice(2);
const project=path.resolve(projectArg),url=new URL(urlArg);
if (!['127.0.0.1','localhost','[::1]'].includes(url.hostname) || url.protocol!=='http:') throw Error('只检验本机预览地址');
const sha=b=>createHash('sha256').update(b).digest('hex');
for(const name of ['index.html','content.js','opening.js','project-data.js','layout.css','runtime.js']){
 const r=await fetch(new URL(name,url),{redirect:'error',signal:AbortSignal.timeout(5000)});
 if(!r.ok||sha(Buffer.from(await r.arrayBuffer()))!==sha(fs.readFileSync(path.join(project,name))))throw Error('预览服务并非当前工程版本：'+name);
}
const binding=JSON.parse(fs.readFileSync(path.join(project,'runtime-binding.json')));
const require=createRequire(path.join(binding.runtime_dir,'package.json'));
const puppeteer=require('puppeteer-core');
const browser=await puppeteer.launch({executablePath:browserPath,headless:true,args:['--autoplay-policy=no-user-gesture-required']});
try{
 const page=await browser.newPage(); const errors=[]; page.on('pageerror',e=>errors.push(e.message));
 await page.setRequestInterception(true);page.on('request',r=>{const u=new URL(r.url());
   if(u.origin===url.origin||['data:','blob:','about:'].includes(u.protocol))r.continue(); else r.abort();});
 const response=await page.goto(url.href,{waitUntil:'domcontentloaded',timeout:45000});
 await page.waitForFunction(()=>{const p=document.querySelector('#player');return p?.ready&&p.assetsReady&&p.duration>0&&p.iframeElement?.contentWindow?.KNOWLEDGE_DIAGNOSTICS?.ready&&!document.querySelector('#toggle').disabled},{timeout:45000});
 const read=()=>page.evaluate(()=>{const p=document.querySelector('#player');return {time:p.currentTime,duration:p.duration,diagnostics:p.iframeElement.contentWindow.KNOWLEDGE_DIAGNOSTICS};});
 const before=await read();await page.click('#toggle');
 await page.waitForFunction(t=>{const p=document.querySelector('#player');return !p.paused&&p.currentTime>t+.35},{timeout:8000},before.time);
 const after=await read();await page.click('#toggle');
 const planPath=path.join(project,'opening-plan.json');let seekProof=[];
 if(fs.existsSync(planPath)){
  const opening=JSON.parse(fs.readFileSync(planPath));const from=opening.start,end=opening.end;
  const targets=[...new Set(opening.actions.map(a=>(a.anchor.start_us+a.anchor.end_us)/2e6))];
  const iframe=await (await page.evaluateHandle(()=>document.querySelector('#player').iframeElement)).asElement().contentFrame();
  const frameDir=path.join(project,'qa/seek-frames');fs.mkdirSync(frameDir,{recursive:true});
  async function at(t){await page.evaluate(t=>document.querySelector('#player').seek(t),t);
    await iframe.waitForFunction(t=>Math.abs(window.__timelines['knowledge-root'].time()-t)<.06,{timeout:5000},t);
    await iframe.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
    // Player scales its 1080p iframe. ElementHandle.screenshot() can crop the
    // unscaled coordinates outside the visible player, making every frame identical.
    const clip=await page.evaluate(()=>{const fr=document.querySelector('#player').iframeElement,w=fr.contentWindow;
      const outer=fr.getBoundingClientRect(),inner=w.document.querySelector('#scene-stage').getBoundingClientRect();
      const sx=outer.width/w.innerWidth,sy=outer.height/w.innerHeight;
      return {x:outer.x+inner.x*sx,y:outer.y+inner.y*sy,width:inner.width*sx,height:inner.height*sy};});
    const pixels=await page.screenshot({clip});fs.writeFileSync(path.join(frameDir,t.toFixed(3)+'.png'),pixels);return sha(pixels);}
  for(const t of targets){await at(from+.02);const forward=await at(t);await at(end-.02);const reverse=await at(t);
    if(forward!==reverse)throw Error('开场乱序跳转得到不同画面：'+t);seekProof.push({at:t,sha256:forward});}
  if(new Set([await at(from+.02),...seekProof.map(x=>x.sha256),await at(end-.02)]).size===1)
    throw Error('开场各时刻的实际画面完全相同；核对动作与截取区域');
 }
 const expected=JSON.parse(fs.readFileSync(path.join(project,'project-data.json'))).duration;
 const result={url:url.href,http_status:response.status(),project_loaded:after.diagnostics.ready,expected_seconds:expected,
   actual_seconds:after.duration,play_control_enabled:true,before_seconds:before.time,after_seconds:after.time,
   authored:after.diagnostics.authored,random_seek:seekProof,errors:[...errors,...after.diagnostics.errors],warnings:after.diagnostics.warnings};
 result.playable=response.ok()&&result.project_loaded&&Math.abs(after.duration-expected)<.12&&after.time>before.time+.35&&!result.errors.length;
 fs.writeFileSync(output,JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 if(!result.playable)process.exitCode=1;
}finally{await browser.close()}
