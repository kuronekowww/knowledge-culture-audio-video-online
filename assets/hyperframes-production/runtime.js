/* Hyperframes 0.9.19: register the root AFTER fonts, images and content are ready. */
(() => {
  const data = window.KNOWLEDGE_DATA;
  const diagnostic = window.KNOWLEDGE_DIAGNOSTICS = {ready:false, errors:[], warnings:data.warnings,
    authored:window.KNOWLEDGE_CONTENT.authored && window.KNOWLEDGE_OPENING.authored, duration:data.duration, fontMode:data.fontMode};
  const mediaReady = el => new Promise((resolve, reject) => {
    if (el.readyState >= 1) return resolve();
    const cleanup = () => { el.removeEventListener('loadedmetadata', ok); el.removeEventListener('error', bad); };
    const ok = () => { cleanup(); resolve(); };
    const bad = () => { cleanup(); reject(new Error('媒体无法加载：'+el.id)); };
    el.addEventListener('loadedmetadata', ok, {once:true});
    el.addEventListener('error', bad, {once:true});
    el.load();
  });
  async function build() {
    if (!window.gsap) throw new Error('本地GSAP缺失');
    const ids = [...document.querySelectorAll('[id]')].map(el=>el.id);
    if (new Set(ids).size !== ids.length) throw new Error('媒体或元素ID重复');
    for (const font of data.fonts) {
      try {
        const loaded = await document.fonts.load(`${font.weight} 54px "${font.family}"`, font.sample);
        if (!loaded.length || loaded.some(face=>face.status !== 'loaded')) throw new Error('font unavailable');
      } catch {
        const notice=document.createElement('p');
        notice.setAttribute('role','alert');
        notice.style.cssText='position:fixed;inset:0;z-index:100;background:white;color:black;padding:60px;font:36px sans-serif';
        notice.textContent='请先在本机安装已核对版本的 '+(font.postscript || font.family)+'，重启浏览器后刷新工程。';
        document.getElementById('knowledge-root').appendChild(notice);
        throw new Error(notice.textContent);
      }
    }
    await document.fonts.ready;
    await Promise.all([...document.images].map(im=>im.decode()));
    await Promise.all([...document.querySelectorAll('audio,video')].map(mediaReady));
    if (Math.abs(document.getElementById('voice').duration - data.bodyDuration) > .12) throw new Error('原声时长与工程不符');
    for (const el of document.querySelectorAll('[data-max-width]')) {
      if (el.scrollWidth > +el.dataset.maxWidth + 1) throw new Error('文字实测超宽：'+el.id);
    }
    const timeline = gsap.timeline({paused:true});
    timeline.to({}, {duration:data.duration}, 0);
    for (const el of document.querySelectorAll('.clip[data-start]')) {
      if (el.tagName === 'AUDIO') continue;
      const start=Number(el.dataset.start), end=start+Number(el.dataset.duration);
      timeline.set(el, {autoAlpha:0}, 0);
      timeline.set(el, {autoAlpha:1}, start);
      timeline.set(el, {autoAlpha:0}, end);
    }
    window.KNOWLEDGE_CONTENT.build(timeline, document.getElementById('scene-stage'), data);
    window.KNOWLEDGE_OPENING.build(timeline, document.getElementById('scene-stage'), data);
    window.__timelines=window.__timelines||{};
    window.__timelines['knowledge-root']=timeline;
    timeline.totalTime(0, false);
    diagnostic.ready=true;
  }
  window.__hf=window.__hf||{};
  window.__hf.buildReady=window.__hf.buildReady||{};
  const ready=build().catch(error=>{ diagnostic.errors.push(error.message); throw error; });
  window.__hf.buildReady['knowledge-assets']=ready;
  window.KNOWLEDGE_READY=ready;
})();
