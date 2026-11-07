// OpenFilm rendering adapter. Uses its native renderer, caption tracks and audio mixer.
import {createHash} from 'node:crypto';
import {existsSync, mkdirSync, readFileSync, readdirSync, renameSync, statSync, writeFileSync} from 'node:fs';
import {join, resolve, relative} from 'node:path';
import {pathToFileURL} from 'node:url';
import {execFileSync} from 'node:child_process';

const [action, packageArg, projectArg, ...args] = process.argv.slice(2);
const pkg=resolve(packageArg), project=resolve(projectArg);
const moduleAt=(name)=>import(pathToFileURL(join(pkg,'src',name+'.mjs')).href);
const {open,filmSound,mix,ffmpeg}=await moduleAt('shared');
const {readFilmFile}=await moduleAt('film-doc');
const {readVtt}=await moduleAt('captions');
const {render}=await moduleAt('render');
const {look}=await moduleAt('look');
const hash=(buffer)=>createHash('sha256').update(buffer).digest('hex');
const fileHash=(file)=>hash(readFileSync(file));
const json=(file)=>JSON.parse(readFileSync(file,'utf8'));
const save=(file,value)=>{mkdirSync(resolve(file,'..'),{recursive:true});const temp=file+'.tmp';writeFileSync(temp,JSON.stringify(value,null,2)+'\n');renameSync(temp,file);};
const log=(value)=>console.log(JSON.stringify(value));
const manifest=json(join(project,'knowledge-project.json'));
const timeline=json(join(project,'knowledge-timeline.json'));
const verifyDir=join(project,'.knowledge','verification');
mkdirSync(verifyDir,{recursive:true});

function inputs() {
  const profilePath=new URL('../layout-profile.json',import.meta.url);
  if(manifest.layout_profile?.sha256!==fileHash(profilePath))
    throw new Error('工程的布局配置已过期；按当前唯一配置新建工程，不覆盖手工编辑版本');
  for(const f of Object.values(manifest.fonts??{}))if(f.embedding==='local'){
    if(!existsSync(f.asset)||fileHash(f.asset)!==f.sha256)
      throw new Error('本机字幕字体缺失或版本变化，禁止静默回退：'+f.postscript);
  }
  const result={};
  const ignore=new Set(['knowledge-project.json','inspection.json','render-result.json','sample-result.json','export-verification.json']);
  const walk=(dir)=>{for(const name of readdirSync(dir).sort()){
    if(name.startsWith('.')||ignore.has(name)||name==='exports')continue;
    const file=join(dir,name);if(statSync(file).isDirectory())walk(file);
    else if(/\.(html|css|js|mjs|json|vtt|png|jpe?g|webp|gif|otf|ttf|woff2?|m4a|mp3|wav|mp4|webm)$/i.test(name))result[relative(project,file)]=fileHash(file);
  }};
  walk(project);return result;
}
function fingerprint(input) {return hash(JSON.stringify(input));}
function probe(file) {return JSON.parse(execFileSync('ffprobe',['-v','error','-show_streams','-show_format','-of','json',file],{encoding:'utf8'}));}
function assertVideo(file,frames,fps) {
  const p=probe(file),v=p.streams.find(s=>s.codec_type==='video');
  if(!v||v.width!==1080||v.height!==1920||v.codec_name!=='h264'||Number(v.nb_frames)!==frames||v.r_frame_rate!==`${fps}/1`)
    throw new Error('视频尺寸/帧率/帧数/编码核验失败：'+file);
  return p;
}
function assertCurrent(expected) {
  if(fingerprint(inputs())!==fingerprint(expected))throw new Error('渲染期间工程发生修改，已停止；保留成功分段，按新版本重新执行');
}

async function inspect() {
  const input=inputs(), source=readFileSync(join(project,'film.html'),'utf8');
  const parsed=readFilmFile(source);
  if(parsed.problems.length)throw new Error(parsed.problems.join('\n'));
  const cues=readVtt(readFileSync(join(project,'captions.vtt'),'utf8')).cues;
  const longest=cues.reduce((best,c)=>!best||c.text.length>best.text.length?c:best,null)?.id;
  const s=await open(project,{playable:true});
  const errors=[],measured=[],snapshots=[];
  let result;
  try {
    const {meta}=s.film;
    if(meta.width!==1080||meta.height!==1920||Math.abs(meta.duration-manifest.duration_seconds)>0.0001)
      throw new Error('OpenFilm实际画布或总时长与交接不符');
    const ends=timeline.frames.map(c=>({kind:'shot',id:c.shot_id??'cover',t:(c.start_us+c.end_us)/2e6}));
    const moments=[{kind:'boundary',t:0},{kind:'boundary',t:0.966666667},{kind:'boundary',t:1},
      ...cues.map(c=>({kind:'caption',id:c.id,t:(c.start+c.end)/2})),...ends,
      {kind:'boundary',t:manifest.body_duration_seconds-1/30},{kind:'boundary',t:manifest.body_duration_seconds},
      {kind:'boundary',t:manifest.duration_seconds-1/30}].sort((a,b)=>a.t-b.t);
    for(let i=0;i<moments.length;i++){
      const point=moments[i];await s.film.seek(point.t);
      const rects=await s.film.page.evaluate(()=>{
        const shown=(el)=>{let p=el;while(p&&p.nodeType===1){const s=getComputedStyle(p);if(s.display==='none'||s.visibility==='hidden'||Number(s.opacity)===0)return false;p=p.parentElement;}return true;};
        const out=[];
        for(const e of document.querySelectorAll('.cue-words'))if(shown(e)){
          const r=e.getBoundingClientRect(),pad=parseFloat(getComputedStyle(e).webkitTextStrokeWidth)/2;out.push({role:'subtitle',text:e.textContent,rect:[r.left-pad,r.top-pad,r.right+pad,r.bottom+pad]});
        }
        for(const frame of document.querySelectorAll('iframe')){
          if(!shown(frame))continue;
          const e=frame.contentDocument?.getElementById('text');if(!e)continue;
          const r=e.getBoundingClientRect(),f=frame.getBoundingClientRect(),pad=parseFloat(frame.contentWindow.getComputedStyle(e).webkitTextStrokeWidth)/2;
          const sx=f.width/frame.contentWindow.innerWidth,sy=f.height/frame.contentWindow.innerHeight;
          out.push({role:(frame.getAttribute('src')??'').split('/').pop().split('.')[0],text:e.textContent,
            rect:[f.left+(r.left-pad)*sx,f.top+(r.top-pad)*sy,f.left+(r.right+pad)*sx,f.top+(r.bottom+pad)*sy]});
        }
        return out;
      });
      for(const row of rects){
        const box=timeline.safe_area.slots_px[row.role]??timeline.safe_area.rect_px;
        const r=row.rect;
        if(r[0]<box[0]-.5||r[1]<box[1]-.5||r[2]>box[2]+.5||r[3]>box[3]+.5)errors.push({t:point.t,...row,allowed:box});
      }
      if(point.t<1&&rects.length)errors.push({t:point.t,error:'首秒出现独立文字'});
      if(point.kind==='caption'&&!rects.some(r=>r.role==='subtitle'))errors.push({t:point.t,error:'应显示字幕时未见字幕'});
      measured.push({...point,rects});
      const saveShot=point.kind==='boundary'||point.kind==='shot'||point.id===longest;
      if(saveShot){const name=`${point.kind}-${point.id??point.t.toFixed(3)}.png`;const file=join(verifyDir,name);writeFileSync(file,await s.film.capture({format:'png'}));snapshots.push({t:point.t,file});}
      if(i%40===0)log({inspection:i,total:moments.length});
    }
    const missing=await s.film.page.evaluate(()=>[...document.fonts].filter(f=>f.status==='error').map(f=>f.family));
    if(missing.length)errors.push({fonts:missing});
    errors.push(...s.film.problems);
    result={status:errors.length?'inspection_failed':'inspection_pending_determinism',input_sha256:input,
      timeline:{width:meta.width,height:meta.height,duration:meta.duration,sounds:meta.sounds},
      measured,errors,snapshots,subtitle_count:cues.length};
    save(join(project,'inspection.json'),result);
    if(errors.length)throw new Error('画面检查失败：'+JSON.stringify(errors.slice(0,5)));
  }finally{await s.close();}
  // Native OpenFilm repeats times in shuffled order to check deterministic page drawing.
  const repeat=await look([project,'1-5'],{count:3});
  save(join(verifyDir,'native-look.json'),repeat);
  if(!repeat.ok)throw new Error('OpenFilm look未通过：'+repeat.lines.join('\n'));
  assertCurrent(input);
  result.status='preview_verified';result.deterministic_seek='passed';
  save(join(project,'inspection.json'),result);
  log({status:'preview_verified',report:join(project,'inspection.json')});
}

async function renderFilm() {
  const workers=Number(args[0]??1),chunkSeconds=Number(args[1]??60);
  const fps=30, totalFrames=Math.round(manifest.duration_seconds*fps);
  const sample=args.length>=4;
  const first=sample?Math.round(Number(args[2])*fps):0;
  const last=sample?Math.round(Number(args[3])*fps):totalFrames;
  if(!Number.isInteger(workers)||workers<1||workers>4||!Number.isFinite(chunkSeconds)||chunkSeconds<1||chunkSeconds>120||first<0||last>totalFrames||last<=first)
    throw new Error('导出参数或范围无效');
  const input=inputs();
  const inspection=existsSync(join(project,'inspection.json'))?json(join(project,'inspection.json')):null;
  if(!inspection||inspection.status!=='preview_verified'||fingerprint(inspection.input_sha256)!==fingerprint(input))
    throw new Error('先对当前工程执行inspect，再导出');
  const format={video:['-c:v','libx264','-preset','veryfast','-crf','18','-colorspace','bt709','-color_primaries','bt709','-color_trc','bt709','-color_range','tv']};
  const signature=hash(JSON.stringify({input,fps,format,runtime:fileHash(new URL(import.meta.url)),version:JSON.parse(readFileSync(join(pkg,'package.json'))).version}));
  const cache=join(project,'.knowledge','renders',signature);
  mkdirSync(cache,{recursive:true});mkdirSync(join(project,'exports'),{recursive:true});
  const chunks=[];
  for(let from=first;from<last;from+=chunkSeconds*fps){
    const to=Math.min(last,from+chunkSeconds*fps),name=`frames-${from}-${to}`,file=join(cache,name+'.mp4'),receipt=join(cache,name+'.json');
    assertCurrent(input);
    if(existsSync(file)&&existsSync(receipt)&&json(receipt).sha256===fileHash(file)){
      assertVideo(file,to-from,fps);log({chunk:name,status:'reused'});
    }else{
      const temp=join(cache,name+'.partial.mp4');
      const result=await render([project,`${from/fps}-${to/fps}`],{out:temp,fps,workers,silent:true,hardware:false,format,
        onProgress:(done,total)=>{if(done%300===0||done===total)log({chunk:name,frames:done,total});}});
      if(result.lines.some(line=>line.startsWith('✗')))throw new Error('渲染产生问题：'+result.lines.join('\n'));
      assertVideo(temp,to-from,fps);assertCurrent(input);renameSync(temp,file);
      save(receipt,{from,to,fps,sha256:fileHash(file),seconds:result.seconds??null,lines:result.lines});
      log({chunk:name,status:'completed',lines:result.lines});
    }
    chunks.push(file);
  }
  // One audio encode for the whole selected range: no per-chunk AAC padding at joins.
  const sound=join(cache,`audio-${first}-${last}.m4a`);
  const s=await open(project,{playable:true});
  try {const clips=await filmSound(s,first/fps);if(!clips.length)throw new Error('成片音轨缺失');await mix(clips,(last-first)/fps,sound,['-c:a','aac','-b:a','192k','-ar','44100']);}
  finally{await s.close();}
  const listing=join(cache,`concat-${first}-${last}.txt`);
  writeFileSync(listing,chunks.map(f=>`file '${relative(cache,f)}'`).join('\n'));
  const stem=sample?`sample-${first/fps}-${last/fps}`:manifest.draft_name.replace(/[\\/:*?"<>|]/g,'_');
  const output=join(project,'exports',stem+'.mp4'),temp=join(project,'exports',stem+'.partial.mp4');
  await ffmpeg(['-f','concat','-safe','0','-i',listing,'-i',sound,'-map','0:v:0','-map','1:a:0','-c','copy','-t',String((last-first)/fps),'-movflags','+faststart',temp]).done;
  const p=assertVideo(temp,last-first,fps),a=p.streams.find(s=>s.codec_type==='audio');
  if(!a||a.codec_name!=='aac'||Math.abs(Number(a.duration)-(last-first)/fps)>.06)throw new Error('原声音轨缺失或时长不符');
  assertCurrent(input);renameSync(temp,output);
  const result={status:sample?'sample_exported':'mp4_exported',output,output_sha256:fileHash(output),
    project_dir:project,input_sha256:input,frames:last-first,fps,duration_seconds:(last-first)/fps,
    streams:p.streams,chunk_count:chunks.length,render_signature:signature,visual_review:'pending',
    source_job:manifest.source_job,backend:'openfilm'};
  save(join(project,sample?'sample-result.json':'render-result.json'),result);log({status:result.status,output,frames:result.frames,chunk_count:chunks.length});
}

try {
  if(action==='inspect')await inspect();
  else if(action==='render')await renderFilm();
  else throw new Error('未知动作：'+action);
} catch(error) { console.error(error.stack??error);process.exitCode=1; }
