/* Reusable body templates. The story layout owns every outer coordinate. */
(() => {
 const KIT=window.KNOWLEDGE_KIT,$=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const names={concept:'概念拆解',dialogue:'情境对话',compare:'两栏对照',chain:'因果与传递',steps:'步骤行动',boundary:'关系界限',sample:'样本偏差',trend:'变化示意'};
 let current,theme,timeline=[],duration=8;
 const stage=(n,s,cls='')=>`<div class="stage ${cls}" data-step="${n}">${s}</div>`;
 function person(o={}){
  const outfit=theme[o.outfit||'accent']||o.outfit||theme.accent,skin=o.skin||'#E9B38F',mood=o.mood||'neutral';
  const hair=o.hair==='bob'?'<path d="M42 77Q30 19 78 13Q130 10 126 86L111 105L48 101Z" fill="#3E4140"/>':'';
  const top=o.hair==='bob'?'<path d="M42 58Q62 49 71 30Q94 44 123 48Q120 13 80 14Q39 14 42 58" fill="#3E4140"/>':'<path d="M42 59Q24 34 52 17Q68 1 95 14Q128 10 126 49L120 63Q104 55 102 34Q80 55 47 46L47 69Z" fill="#273947"/>';
  const mouth=mood==='angry'?'M69 89Q81 78 94 88':mood==='sad'?'M69 88Q81 82 94 89':'M67 83Q81 98 98 82';
  return `<svg viewBox="0 0 160 185" aria-label="示意人物"><g stroke="white" stroke-width="7" stroke-linejoin="round" paint-order="stroke fill"><path d="M23 177L27 138Q28 113 62 107L101 107Q132 114 136 139L140 177Z" fill="${esc(outfit)}"/>${hair}<path d="M65 95L63 115Q82 131 100 114L97 94Z" fill="#D49B79"/><ellipse cx="82" cy="64" rx="40" ry="48" fill="${skin}"/>${top}</g><g stroke="#A36B51" fill="none" stroke-width="3" stroke-linecap="round"><path d="M81 70L77 77L83 78"/><path d="${mouth}"/></g><g fill="#273947"><circle cx="65" cy="64" r="3"/><circle cx="99" cy="64" r="3"/></g>${mood==='angry'?'<path d="M58 52L70 57M94 57L106 52" stroke="#273947" stroke-width="3"/>':''}<path d="M45 138L42 172M118 138L121 172" stroke="#ffffff45" stroke-width="4" stroke-linecap="round"/></svg>`;
 }
 const figure=o=>`<div class="person">${person(o)}<div class="person-label">${esc(o.label||'')}</div></div>`;
 function icon(kind){return kind==='coin'?`<svg class="icon" viewBox="0 0 80 80"><circle cx="40" cy="40" r="33" fill="${theme.highlight}" stroke="white" stroke-width="5"/><path d="M26 20L40 38L54 20M22 39H58M22 49H58M40 38V63" fill="none" stroke="${theme.ink}" stroke-width="5"/></svg>`:`<svg class="icon" viewBox="0 0 80 80"><path d="M15 62L52 23M45 16L64 35" stroke="${theme.ink}" stroke-width="12" stroke-linecap="round"/><path d="M14 64H68" stroke="${theme.accent}" stroke-width="9" stroke-linecap="round"/><circle cx="59" cy="23" r="15" fill="${theme.highlight}" stroke="white" stroke-width="4"/></svg>`}
 function renderBody(e){const d=e.data;
  const end=stage(4,esc(d.takeaway),'takeaway');
  if(e.type==='concept')return stage(0,'','halo')+stage(0,figure({...d.person,label:d.person.label||'一个人'}),'concept-person')+stage(1,`<div class="pill">${esc(d.observed)}</div>`,'concept-known')+stage(2,'→','relation-arrow')+stage(2,'从一个特征，推到全部？','relation-caption')+stage(3,d.inferred.map(x=>`<div class="chip">${esc(x)}</div>`).join(''),'concept-infer')+end;
  if(e.type==='dialogue')return stage(0,`${esc(d.lines[0])}<em>${esc(d.notes[0])}</em>`,'card speech speech-old')+stage(1,`${esc(d.lines[1])}<em>${esc(d.notes[1])}</em>`,'card speech speech-new')+stage(0,figure(d.left),'talk-person talk-left')+stage(1,figure(d.right),'talk-person talk-right')+stage(2,'换个说法<b>→</b>','dialog-arrow')+end;
  if(e.type==='compare')return `<div class="compare-grid">${[d.left,d.right].map((x,i)=>stage(i,`${icon(x.icon)}<h3>${esc(x.title)}</h3>${x.items.map(y=>`<p>${esc(y)}</p>`).join('')}`,'card compare-card')).join('')}</div>`+end;
  if(e.type==='chain'){const gap=(936-174*d.nodes.length)/(d.nodes.length-1);return `<div class="chain-grid">${d.nodes.map((x,i)=>stage(i,figure({hair:i%2?'bob':'short',outfit:i%2?'secondary':'accent',...x}))).join('')}</div>${d.nodes.slice(0,-1).map((x,i)=>`<div class="stage chain-arrow" data-step="${i+1}" style="left:${(174+gap)*i+174+(gap-92)/2}px">${esc(x.verb)}<b>→</b></div>`).join('')}`+end;}
  if(e.type==='steps')return `<div class="steps-grid">${d.items.map((s,i)=>stage(i,`<div class="number">0${i+1}</div><div class="step-title">${esc(s)}</div>`,'card step-card')).join('')}</div>`+end;
  if(e.type==='boundary')return stage(0,`<h3>${esc(d.self)}</h3><p>${d.self_items.map(esc).join(' · ')}</p>`,'boundary-circle')+stage(1,`<h3>${esc(d.other)}</h3><p>${d.other_items.map(esc).join(' · ')}</p>`,'boundary-circle')+stage(2,esc(d.center),'boundary-bridge')+end;
  if(e.type==='sample')return `<div class="sample-grid">${[d.visible,d.missing].map((x,i)=>stage(i,`<h3>${esc(x)}</h3><div class="sample-people">${[0,1,2].map(j=>person({hair:e.character_variant||(j%2?'bob':'short'),outfit:i?'secondary':'accent'})).join('')}</div><small>${i?'也属于完整样本':'更容易被我们听到'}</small>`,'card sample-panel')).join('')}</div>`+end;
  if(e.type==='trend')return `<svg class="trend-svg" viewBox="0 0 936 330"><path class="axis" d="M70 25V275H885"/><text x="675" y="320">${esc(d.axis)} →</text><path class="line" data-path="1" stroke="${theme.accent}" d="M96 55C230 62 270 170 410 202S690 247 859 251"/><path class="line" data-path="2" stroke="${theme.secondary}" d="M96 251C269 245 345 192 461 141S716 61 859 54"/><text class="trend-label" x="560" y="48">${esc(d.up)}</text><text class="trend-label" x="560" y="241">${esc(d.down)}</text></svg>`+end;
  throw Error('Unsupported template: '+e.type);
 }
 function canonical(id,text,style){const e=$(id),s=style.pixel_style,span=e.querySelector('span'),count=text.split('\n').length,h=s.font_size_px*s.line_height*count,pad=s.stroke_px/2;
  const x=s.left_top_px?s.left_top_px[0]:s.center_px[0]-s.max_width_px/2,y=s.left_top_px?s.left_top_px[1]:s.center_px[1]-h/2;
  Object.assign(e.style,{left:x+'px',top:y+'px',width:s.max_width_px+'px',height:h+'px',justifyContent:id==='album'?'flex-start':'center'});
  const stroke=s.stroke_alpha<1?`rgba(0,0,0,${s.stroke_alpha})`:s.stroke_color;
  Object.assign(span.style,{fontFamily:id==='subtitle'?'StorySubtitle':'StoryTitle',fontSize:s.font_size_px+'px',fontWeight:String(s.weight),letterSpacing:s.letter_spacing_px+'px',lineHeight:String(s.line_height),color:s.color,webkitTextStroke:`${s.stroke_px}px ${stroke}`,transform:`scaleX(${s.resolved_scale_x})`,transformOrigin:id==='album'?'left center':'center center',textAlign:id==='album'?'left':'center'});span.textContent=text;
  if(s.shadow)span.style.textShadow=`${s.shadow.x_px}px ${s.shadow.y_px}px ${s.shadow.blur_px}px rgba(0,0,0,${s.shadow.alpha})`;
 }
 function box(id,b){Object.assign($(id).style,{left:b[0]+'px',top:b[1]+'px',width:b[2]-b[0]+'px',height:b[3]-b[1]+'px'})}
 async function setExample(example,albumOverride){
  current=typeof example==='string'?KIT.examples.find(x=>x.id===example):structuredClone(example);if(!current)throw Error('Example missing');
  const a=KIT.albums[albumOverride||current.album_id];theme=window.KnowledgePalette(current.theme_override?.accent||a.theme.accent);for(const [k,v] of Object.entries(theme))document.documentElement.style.setProperty('--'+k.replaceAll('_','-'),v);document.documentElement.style.setProperty('--tint',theme.accent+'22');
  duration=current.demo_duration||8;timeline=current.stage_times||[0,.8,2,3.4,4.8];
  for(const role of ['title_line1','title_line2','subtitle']){const row=current.styles[role];canonical(role,row.text,row.style)}
  canonical('album',a.display,a.style);$('album-cover').src=a.cover;box('album-cover',KIT.layout.boxes_px.album_cover);box('source-label',KIT.layout.boxes_px.source_label);
  const ps=KIT.playback,b=KIT.layout.boxes_px.playback;Object.assign($('playback').style,{left:b[0]+'px',top:b[1]+'px',width:ps.width+'px',height:ps.height+'px',backgroundSize:`${ps.width*ps.columns}px ${ps.height*ps.rows}px`,transform:`scale(${(b[2]-b[0])/ps.width})`});
  $('module-label').textContent=names[current.type];$('module-heading').textContent=current.heading;$('module-body').innerHTML=renderBody(current);$('module-note').textContent=current.data.note||'';$('animation').classList.toggle('has-note',!!current.data.note);
  await Promise.all([document.fonts.load('400 78px StoryTitle'),document.fonts.load('500 54px StorySubtitle'),document.fonts.load('700 43px Body'),document.fonts.load('400 30px Body')]);await document.fonts.ready;
  await Promise.all([...document.images].map(im=>im.decode()));window.film.duration=duration;window.DURATION=duration;frame(duration-.1);return current;
 }
 function frame(t){const p=x=>Math.min(1,Math.max(0,x));for(const e of document.querySelectorAll('[data-step]')){const n=+e.dataset.step,q=n===0?1:1-Math.pow(1-p((t-(timeline[n]??0))/.45),3);e.style.opacity=q;e.style.translate=`0 ${(1-q)*18}px`;}
  for(const e of document.querySelectorAll('[data-path]')){const l=e.getTotalLength(),n=+e.dataset.path,q=p((t-(timeline[n]??0))/1.3);e.style.strokeDasharray=l;e.style.strokeDashoffset=l*(1-q)}
  const s=KIT.playback,d=s.durations_ms,total=d.reduce((a,b)=>a+b,0);let ms=((t*1000)%total+total)%total,i=0;while(i<d.length-1&&ms>=d[i])ms-=d[i++];$('playback').style.backgroundPosition=`${-(i%s.columns)*s.width}px ${-Math.floor(i/s.columns)*s.height}px`;
 }
 window.setExample=setExample;window.seek=frame;window.setGuides=on=>$('guides').style.display=on?'block':'none';window.film={frame,duration:8,width:1080,height:1920};
 const q=new URLSearchParams(location.search);window.READY=setExample(q.get('id')||'K01',q.get('album')||undefined);window.film.ready=window.READY;
 window.addEventListener('message',async ev=>{if(ev.source!==parent)return;const m=ev.data||{};if(m.kind==='seek')frame(m.time);if(m.kind==='guides')window.setGuides(m.on);if(m.kind==='example'){await setExample(m.example);parent.postMessage({kind:'scene-ready'},'*');}});
 window.READY.then(()=>parent.postMessage({kind:'scene-ready'},'*'));
})();
