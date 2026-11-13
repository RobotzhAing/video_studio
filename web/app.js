'use strict';

const $ = id => document.getElementById(id);
const state = {
  media: [],
  taskFolders: [],
  ai: {base_url:'local://builtin', model:'builtin-editor', api_key:'', audio_model:'', tts_model:'', tts_voice:''}
};
let toastTimer;
let editingTaskFolderId = null;
let processing = false;
let currentPlayback = null;
const resultsById = new Map();
const revisionControls = new Map();
const resultCards = new Map();
const mediaChecks = new Map();
const mixChecks = new Map();
const mediaSelection = new Set();
const mixSelection = new Set();
const PAGE_SIZE = 10;
const pages = {media: 1, mix: 1, folders: 1};
const mixArticles = [];
const mixArticleIds = new WeakMap();
let deleting = false;

function toast(message) {
  const el = $('toast');
  el.textContent = message;
  el.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), 4200);
}
async function request(url, options={}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
  return data;
}
function post(url, data) {
  return request(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
}
function fmt(seconds) {
  const n = Math.max(0, Math.floor(Number(seconds)||0));
  return `${String(Math.floor(n/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`;
}
function item(id) { return state.media.find(asset => asset.id === id); }
function button(label, className='button subtle compact') {
  const el = document.createElement('button');
  el.type = 'button';
  el.className = className;
  el.textContent = label;
  return el;
}
function jobPath(id, action='') {
  if (!/^[0-9a-f]{32}$/.test(id)) throw new Error('成片编号无效');
  return `/api/jobs/${id}${action}`;
}
function playMedia({type, url, name}) {
  const ids = {video:'video-preview', image:'image-preview', audio:'audio-preview'};
  const player = $(ids[type]);
  if (!player) return;
  for (const id of Object.values(ids)) {
    const other = $(id);
    if (id !== ids[type]) {
      other.pause?.();
      other.classList.add('hidden');
      other.removeAttribute('src');
      other.load?.();
    }
  }
  $('preview-name').textContent = name;
  $('preview-name').title = name;
  $('preview-empty').classList.add('hidden');
  $('preview-error').classList.add('hidden');
  player.classList.remove('hidden');
  const sameFile = currentPlayback?.url === url;
  currentPlayback = {url, player};
  if (!sameFile) player.src = url;
  player.play?.().catch(() => {});
  if (window.matchMedia('(max-width: 720px)').matches) {
    $('player-title').scrollIntoView({block:'start', behavior:'smooth'});
  }
}
function clearPlayback(url) {
  if (currentPlayback?.url !== url) return;
  const {player} = currentPlayback;
  currentPlayback = null;
  player.pause?.();
  player.removeAttribute('src');
  player.load?.();
  player.classList.add('hidden');
  $('preview-name').textContent = '预览';
  $('preview-name').removeAttribute('title');
  $('preview-error').classList.add('hidden');
  $('preview-empty').classList.remove('hidden');
}
function pageSlice(prefix, items) {
  pages[prefix] = Math.min(pages[prefix], Math.max(1, Math.ceil(items.length / PAGE_SIZE)));
  const start = (pages[prefix] - 1) * PAGE_SIZE;
  return items.slice(start, start + PAGE_SIZE);
}
function retainSelection(selection, pageIds) {
  const visible = new Set(pageIds);
  for (const id of selection) if (!visible.has(id)) selection.delete(id);
}
function renderPager(prefix, total, render, selection) {
  const nav = $(prefix + '-pagination');
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const current = pages[prefix];
  nav.classList.toggle('hidden', pageCount === 1);
  nav.replaceChildren();
  if (pageCount === 1) return;
  const numbers = [...new Set([
    1, pageCount, ...Array.from({length: 5}, (_, i) => Math.max(1, Math.min(current - 2, pageCount - 4)) + i)
      .filter(n => n <= pageCount)
  ])].sort((a, b) => a - b);
  function go(target) {
    if (deleting || target < 1 || target > pageCount || target === current) return;
    pages[prefix] = target;
    selection?.clear();
    render();
    nav.querySelector('[aria-current="page"]')?.focus();
  }
  function addButton(text, name, target, disabled = false) {
    const control = button(text);
    control.disabled = disabled;
    control.setAttribute('aria-label', name);
    control.addEventListener('click', () => go(target));
    nav.append(control);
    return control;
  }
  addButton('上一页', '上一页', current - 1, current === 1);
  let previous = 0;
  for (const n of numbers) {
    if (n > previous + 1) {
      const gap = document.createElement('span');
      gap.textContent = '…';
      gap.setAttribute('aria-hidden', 'true');
      nav.append(gap);
    }
    const control = addButton(String(n), `第 ${n} 页`, n);
    if (n === current) control.setAttribute('aria-current', 'page');
    previous = n;
  }
  addButton('下一页', '下一页', current + 1, current === pageCount);
}
function togglePageSelection(prefix) {
  if (processing || deleting) return;
  const [selection, checks] = prefix === 'media' ? [mediaSelection, mediaChecks] : [mixSelection, mixChecks];
  selection.clear();
  if ($(prefix + '-select-page').checked) {
    for (const id of checks.keys()) selection.add(id);
  }
  updateDeleteControls();
}
function selectionCheck(id, name, selection, checks) {
  const input = document.createElement('input');
  input.type = 'checkbox';
  input.className = 'bulk-check';
  input.setAttribute('aria-label', `勾选删除：${name}`);
  checks.set(id, input);
  input.checked = selection.has(id);
  input.addEventListener('change', () => {
    if (input.checked) selection.add(id);
    else selection.delete(id);
    updateDeleteControls();
  });
  return input;
}
function updateDeleteControls() {
  for (const [prefix, selection, checks, unit] of [
    ['media', mediaSelection, mediaChecks, '项'], ['mix', mixSelection, mixChecks, '条']
  ]) {
    $(prefix + '-selection').textContent = `已选 ${selection.size} ${unit}`;
    const all = $(prefix + '-select-page');
    all.disabled = processing || deleting || !checks.size;
    all.checked = checks.size > 0 && selection.size === checks.size;
    all.indeterminate = selection.size > 0 && selection.size < checks.size;
    $(prefix + '-delete-selected').disabled = processing || deleting || !selection.size;
    for (const [id, check] of checks) {
      check.checked = selection.has(id);
      check.disabled = processing || deleting;
    }
  }
}
function setDeleteStatus(prefix, message, failed=false) {
  const status = $(prefix + '-delete-status');
  status.textContent = message;
  status.classList.toggle('hidden', !message);
  status.classList.toggle('bulk-error', failed);
}
async function deleteSelected(prefix) {
  const isMedia = prefix === 'media';
  const selection = isMedia ? mediaSelection : mixSelection;
  if (processing || deleting || !selection.size) return;
  deleting = true;
  updateTaskControls();
  setDeleteStatus(prefix, '正在永久删除…');
  try {
    const ids = [...selection];
    for (const id of ids) clearPlayback(isMedia
      ? `/api/media/${encodeURIComponent(id)}/stream` : jobPath(id, '/preview'));
    const path = isMedia ? '/api/media/bulk-delete' : '/api/completed-mixes/bulk-delete';
    const result = await post(path, {ids});
    const deleted = new Set(Array.isArray(result.deleted) ? result.deleted : []);
    const errors = Array.isArray(result.errors) ? result.errors : [];
    const errorDetails = errors.slice(0, 3).map(({id, error}) => {
      const name = isMedia ? item(id)?.name : resultsById.get(id)?.output_filename;
      return name ? `${name}：${error}` : error;
    });
    for (const id of deleted) selection.delete(id);
    if (isMedia) {
      for (const id of deleted) clearPlayback(`/api/media/${encodeURIComponent(id)}/stream`);
      state.media = state.media.filter(media => !deleted.has(media.id));
      renderMedia();
    } else {
      for (const id of deleted) {
        clearPlayback(jobPath(id, '/preview'));
        const article = resultCards.get(id);
        const position = mixArticles.indexOf(article);
        if (position !== -1) mixArticles.splice(position, 1);
        article?.remove();
        resultCards.delete(id);
        mixChecks.delete(id);
        revisionControls.delete(id);
        resultsById.delete(id);
      }
      renderMixPage();
    }
    const noun = isMedia ? '项素材' : '条成片';
    const message = `已永久删除 ${deleted.size} ${noun}` + (errors.length
      ? `，${errors.length} 项未删除。 ${errorDetails.join('；')}` : '。');
    setDeleteStatus(prefix, message, !!errors.length);
    const refreshed = await Promise.allSettled(isMedia
      ? [loadMedia(), loadTaskFolders(), loadCompleted()] : [loadCompleted()]);
    if (refreshed.some(entry => entry.status === 'rejected')) {
      setDeleteStatus(prefix, message + '部分列表未能刷新，重新打开应用后可重试。', true);
    }
  } catch (error) {
    setDeleteStatus(prefix, `删除请求失败：${error.message}`, true);
  } finally {
    deleting = false;
    updateTaskControls();
  }
}
for (const id of ['video-preview', 'image-preview', 'audio-preview']) {
  $(id).addEventListener('error', () => {
    if (currentPlayback?.player !== $(id)) return;
    $('preview-error').textContent = '无法在播放器中打开该文件。';
    $('preview-error').classList.remove('hidden');
  });
}
function renderMedia() {
  const list = $('media-list');
  list.replaceChildren();
  mediaChecks.clear();
  const visible = pageSlice('media', state.media);
  retainSelection(mediaSelection, visible.map(media => media.id));
  $('media-count').textContent = `${state.media.length} 项`;
  $('media-empty').classList.toggle('hidden', !!state.media.length);
  for (const media of visible) {
    const card = document.createElement('article');
    card.className = 'asset';
    const play = button(media.type==='image' ? '查看' : '播放');
    play.setAttribute('aria-label', `${play.textContent}素材：${media.name}`);
    play.addEventListener('click', () => playMedia({
      type: media.type, name: media.name, url: `/api/media/${encodeURIComponent(media.id)}/stream`
    }));
    const name = document.createElement('span');
    name.className = 'asset-name';
    name.textContent = media.name;
    name.title = media.name;
    const meta = document.createElement('small');
    meta.textContent = `${{video:'视频', image:'图片', audio:'音频'}[media.type] || '素材'} · ${fmt(media.duration)}`;
    const info = document.createElement('div');
    info.className = 'asset-info';
    info.append(name, meta);
    card.append(selectionCheck(media.id, media.name, mediaSelection, mediaChecks), info, play);
    list.append(card);
  }
  renderPager('media', state.media.length, renderMedia, mediaSelection);
  renderTaskFolders();
}
async function loadMedia() {
  const data = await request('/api/media');
  state.media = Array.isArray(data) ? data : data.media || [];
  renderMedia();
}
function renderTaskFolders(){
  const list=$('task-folder-list');if(!list)return;list.replaceChildren();
  const supported=state.media.filter(asset=>asset.type==='video'||asset.type==='image');
  $('task-folder-empty').classList.toggle('hidden',state.taskFolders.length>0);
  for(const folder of pageSlice('folders', state.taskFolders)){
    const card=document.createElement('article');card.className='task-folder-card';
    const heading=document.createElement('div');heading.className='task-folder-card-heading';
    const title=document.createElement('strong');title.textContent=folder.name;title.title=folder.name;
    const count=document.createElement('span');const available=(folder.media_ids||[]).filter(id=>supported.some(asset=>asset.id===id)).length;
    count.className='task-folder-count';count.textContent=`${available} 项素材`;
    heading.append(title,count);
    const prompt=document.createElement('p');prompt.className='task-folder-prompt';prompt.textContent=folder.prompt||'尚未填写混剪要求';
    const reference=folder.reference_video_id ? state.media.find(asset=>asset.id===folder.reference_video_id) : null;
    if(reference){const ref=document.createElement('p');ref.className='task-folder-reference';ref.textContent=`参考视频：${reference.name}（${reference.has_audio?'参考画面和音频':'参考画面'}）`;ref.title=reference.name;card.append(ref);}
    const actions=document.createElement('div');actions.className='task-folder-actions';
    const edit=document.createElement('button');edit.type='button';edit.className='button subtle compact';edit.textContent='编辑';edit.addEventListener('click',()=>openTaskFolderDialog(folder));
    const remove=document.createElement('button');remove.type='button';remove.className='button subtle compact';remove.textContent='删除';remove.addEventListener('click',()=>void removeTaskFolder(folder));
    actions.append(edit,remove);card.append(heading,prompt,actions);list.append(card);
  }
  renderPager('folders', state.taskFolders.length, renderTaskFolders);
  updateTaskControls();
}
async function loadTaskFolders(){
  const data=await request('/api/task-folders');
  state.taskFolders=Array.isArray(data.folders)?data.folders:[];renderTaskFolders();
}
function openTaskFolderDialog(folder=null){
  editingTaskFolderId=folder?.id||null;
  $('task-folder-dialog-title').textContent=folder?'编辑混剪任务文件夹':'新建混剪任务文件夹';
  $('task-folder-name').value=folder?.name||'';$('task-folder-prompt').value=folder?.prompt||'';$('task-folder-form-error').textContent='';
  const referenceSelect=$('task-folder-reference');
  referenceSelect.replaceChildren();
  const noReference=document.createElement('option');noReference.value='';noReference.textContent='不使用参考视频';referenceSelect.append(noReference);
  const referenceCandidates=state.media.filter(asset=>asset.type==='video');
  for(const asset of referenceCandidates){const option=document.createElement('option');option.value=asset.id;option.textContent=asset.name;referenceSelect.append(option);}
  referenceSelect.value=folder?.reference_video_id||'';
  const selected=new Set(folder?.media_ids||[]),assets=$('task-folder-assets-list');assets.replaceChildren();
  const candidates=state.media.filter(asset=>asset.type==='video'||asset.type==='image');
  if(!candidates.length){const empty=document.createElement('p');empty.className='hint';empty.textContent='请先导入视频或图片素材。';assets.append(empty);}
  for(const asset of candidates){
    const label=document.createElement('label');label.className='task-folder-asset-option';
    const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.value=asset.id;checkbox.checked=selected.has(asset.id);
    const name=document.createElement('span');name.textContent=asset.name;name.title=asset.name;
    const meta=document.createElement('small');meta.textContent=`${asset.display_width||asset.width||'—'}×${asset.display_height||asset.height||'—'} · ${fmt(asset.duration)}`;
    label.append(checkbox,name,meta);assets.append(label);
  }
  const syncReferenceSelection=()=>{
    const selectedReference=referenceSelect.value;
    assets.querySelectorAll('input[type="checkbox"]').forEach(input=>{
      const isReference=input.value===selectedReference;
      if(isReference)input.checked=false;
      input.disabled=isReference;
      input.closest('label')?.classList.toggle('task-folder-reference-source',isReference);
    });
  };
  referenceSelect.onchange=syncReferenceSelection;
  syncReferenceSelection();
  $('task-folder-dialog').showModal();$('task-folder-name').focus();
}
async function saveTaskFolder(){
  const name=$('task-folder-name').value.trim(),prompt=$('task-folder-prompt').value.trim();
  const media_ids=Array.from($('task-folder-assets-list').querySelectorAll('input[type="checkbox"]:checked'),input=>input.value);
  const reference_video_id=$('task-folder-reference').value||null;
  if(!name){$('task-folder-form-error').textContent='请填写文件夹名称。';return;}
  if(!prompt){$('task-folder-form-error').textContent='请填写该任务的混剪要求。';return;}
  if(!media_ids.length){$('task-folder-form-error').textContent='至少选择一个视频或图片素材。';return;}
  const button=$('task-folder-save');button.disabled=true;
  try{
    if(editingTaskFolderId)await request(`/api/task-folders/${encodeURIComponent(editingTaskFolderId)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,prompt,media_ids,reference_video_id})});
    else await post('/api/task-folders',{name,prompt,media_ids,reference_video_id});
    await loadTaskFolders();$('task-folder-dialog').close();toast(editingTaskFolderId?'任务文件夹已更新':'任务文件夹已创建');
  }catch(error){$('task-folder-form-error').textContent=`保存失败：${error.message}`;}
  finally{button.disabled=false;}
}
async function removeTaskFolder(folder){
  if(!window.confirm(`删除任务文件夹“${folder.name}”？素材文件不会删除。`))return;
  try{await request(`/api/task-folders/${encodeURIComponent(folder.id)}`,{method:'DELETE'});await loadTaskFolders();toast('任务文件夹已删除');}
  catch(error){toast(`删除任务文件夹失败：${error.message}`);}
}

function updateTaskControls() {
  $('ai-batch-export').disabled = processing || deleting || !state.taskFolders.length;
  updateDeleteControls();
  for (const [id, controls] of revisionControls) {
    const job = resultsById.get(id);
    const folder = state.taskFolders.find(f => f.id === job?.task_folder_id);
    const unavailable = !folder || job.can_revise === false;
    controls.submit.disabled = processing || deleting || unavailable;
    controls.input.disabled = unavailable;
    controls.status.hidden = !unavailable;
    controls.status.textContent = !folder
      ? '找不到此成片的原任务文件夹。仍可播放或下载。'
      : '此成片缺少可用于修改的原始方案。';
  }
}
function renderMixPage() {
  const list = $('ai-batch-result');
  const visible = pageSlice('mix', mixArticles);
  const allChecks = mixChecks;
  const visibleIds = visible.map(article => mixArticleIds.get(article)).filter(Boolean);
  retainSelection(mixSelection, visibleIds);
  allChecks.clear();
  for (const article of visible) {
    const id = mixArticleIds.get(article);
    if (id) allChecks.set(id, article.querySelector('input.bulk-check'));
  }
  if (list.children.length !== visible.length || visible.some((article, i) => list.children[i] !== article)) {
    list.replaceChildren(...visible);
  }
  $('ai-result-empty').classList.toggle('hidden', mixArticles.length > 0);
  renderPager('mix', mixArticles.length, renderMixPage, mixSelection);
  updateDeleteControls();
}
function makeResultCard(name, status='等待处理', render=true) {
  const article = document.createElement('article');
  article.className = 'result-card';
  const title = document.createElement('h4');
  title.textContent = name;
  const message = document.createElement('p');
  message.className = 'result-status';
  message.setAttribute('role','status');
  message.textContent = status;
  const heading = document.createElement('div');
  heading.className = 'result-heading';
  heading.append(title);
  article.append(heading, message);
  mixArticles.unshift(article);
  if (render) renderMixPage();
  return {article, heading, title, message};
}
function setResultStatus(card, message, phase='working') {
  card.message.textContent = message;
  card.article.dataset.phase = phase;
}
function addResultNote(card, text) {
  if (!text) return;
  const note = document.createElement('p');
  note.className = 'result-note';
  note.textContent = text;
  card.article.append(note);
}
function showCompleted(job, card=null, plan=null) {
  if (!job || !/^[0-9a-f]{32}$/.test(job.id)) throw new Error('成片编号无效');
  if (resultsById.has(job.id)) return;
  const fileName = job.output_filename || '混剪成片.mp4';
  card ||= makeResultCard(fileName, undefined, false);
  card.title.textContent = fileName;
  const dimensions = job.width && job.height ? ` · ${job.width}×${job.height}` : '';
  const captionCount = plan?.textClips?.length;
  setResultStatus(card, `已完成${dimensions}${captionCount ? ` · ${captionCount} 条字幕` : ''}`, 'complete');
  resultsById.set(job.id, job);
  resultCards.set(job.id, card.article);
  mixArticleIds.set(card.article, job.id);
  card.heading.prepend(selectionCheck(job.id, fileName, mixSelection, mixChecks));
  const mix = plan?.audioMix;
  if (mix) addResultNote(card, [
    mix.speechLocale && `配音：${mix.speechLocale}`,
    mix.subtitleLocale && `字幕：${mix.subtitleLocale}`,
    mix.musicStyle && `背景音乐：${mix.musicStyle}`
  ].filter(Boolean).join(' · '));
  if (job.revision_instruction) {
    const parentName = resultsById.get(job.revision_of)?.output_filename;
    if (parentName) addResultNote(card, `基于：${parentName}`);
    addResultNote(card, `修改意见：${job.revision_instruction}`);
  }
  if (Array.isArray(job.warnings) && job.warnings.length) addResultNote(card, job.warnings.join('；'));

  const actions = document.createElement('div');
  actions.className = 'result-actions';
  const play = button('▷ 播放', 'button primary compact');
  play.setAttribute('aria-label', `播放成片：${fileName}`);
  play.addEventListener('click', () => playMedia({type:'video', name:fileName, url:jobPath(job.id, '/preview')}));
  const download = document.createElement('a');
  download.className = 'button subtle compact';
  download.href = jobPath(job.id, '/download');
  download.download = fileName;
  download.textContent = '下载';
  download.setAttribute('aria-label', `下载成片：${fileName}`);
  actions.append(play, download);

  const form = document.createElement('form');
  form.className = 'revision-form';
  form.setAttribute('aria-label', `修改成片：${fileName}`);
  const label = document.createElement('label');
  label.textContent = '修改意见';
  const input = document.createElement('textarea');
  input.rows = 3;
  input.maxLength = 2000;
  input.required = true;
  input.placeholder = '例如：开头节奏更快，把配乐改成爵士，其他保留。';
  label.append(input);
  const submit = button('修改混剪');
  submit.type = 'submit';
  const status = document.createElement('p');
  status.className = 'hint';
  status.hidden = true;
  form.append(label, submit, status);
  form.addEventListener('submit', event => {
    event.preventDefault();
    void reviseMix(job, input);
  });
  card.article.append(actions, form);
  revisionControls.set(job.id, {input, submit, status});
  if (plan) {
    renderMixPage();
    updateTaskControls();
  }
}
async function loadCompleted() {
  const data = await request('/api/completed-mixes');
  const history = Array.isArray(data.jobs) ? data.jobs : [];
  for (const job of history.slice().reverse()) {
    if (!job || !/^[0-9a-f]{32}$/.test(job.id)) continue;
    if (resultsById.has(job.id)) Object.assign(resultsById.get(job.id), job);
    else showCompleted(job);
  }
  renderMixPage();
  updateTaskControls();
}
async function configuredAi() {
  await aiSettingsReady;
  const settings = Object.fromEntries(Object.entries(state.ai).filter(([,value]) => value));
  if (!settings.base_url || !settings.model || !settings.api_key || settings.base_url === 'local://builtin') {
    toast('完整混剪需要支持配音的 AI 接口，请先完成 AI 设置');
    void openSettings();
    return null;
  }
  return settings;
}
async function waitForRender(id, card) {
  const started = Date.now();
  for (;;) {
    const job = await request(jobPath(id));
    if (job.status === 'done') return job;
    if (job.status === 'error' || job.status === 'failed') throw new Error(job.message || job.error || '渲染失败');
    if (Date.now() - started > 30 * 60 * 1000) throw new Error('渲染仍在继续；请稍后重新打开应用查看结果。');
    const progress = Number.isFinite(job.progress) ? ` ${job.progress}%` : '';
    setResultStatus(card, `渲染中：${job.message || '正在处理'}${progress}`);
    await new Promise(resolve => setTimeout(resolve, 1200));
  }
}
async function createMix(folder, settings, card, revision=null) {
  let selected = [];
  if (!revision) {
    selected = [...new Set(folder.media_ids || [])].map(item).filter(asset => asset && ['video','image'].includes(asset.type));
    if (!folder.prompt?.trim()) throw new Error('未填写混剪要求');
    if (!selected.length) throw new Error('没有可用的视频或图片素材');
    if (selected.length > 100) throw new Error('单个任务最多使用 100 项素材');
    const reference = folder.reference_video_id ? item(folder.reference_video_id) : null;
    if (reference?.has_audio && !settings.audio_model) throw new Error('参考视频含音轨，请在 AI 设置填写音频理解模型');
  }
  setResultStatus(card, revision ? '正在根据修改意见重新混剪…' : '正在分析素材、生成配音和音乐…');
  const plan = await post('/api/ai-plan', {
    ...settings, complete_mix:true,
    ...(revision ? {revision} : {
      prompt:folder.prompt.trim(),
      clips:selected.map(asset => ({id:asset.id,start:0,end:Number(asset.duration),speed:1})),
      reference_video_id:folder.reference_video_id || null
    })
  });
  if (revision) {
    const ids = Array.isArray(plan.revisionMediaIds) ? plan.revisionMediaIds : [];
    selected = ids.map(item);
    if (!selected.length || selected.some(asset => !asset)) throw new Error('找不到此成片的原始素材');
  }
  const payload = VideoFusionBatchTasks.createRenderPayload(folder, selected, plan, {completeMix:true});
  setResultStatus(card, `正在准备 ${payload.width}×${payload.height} 成片…`);
  const queued = await post('/api/render', {
    ...payload, taskFolderId:folder.id,
    ...(revision ? {revisionOf:revision.jobId, revisionInstruction:revision.instruction} : {}),
    revisionPlan:{clips:plan.clips,textClips:plan.textClips,audioMix:plan.audioMix}
  });
  const rendered = await waitForRender(queued.job_id, card);
  showCompleted({
    ...rendered, id:queued.job_id, task_folder_id:folder.id, can_revise:true,
    ...(revision ? {revision_of:revision.jobId, revision_instruction:revision.instruction} : {})
  }, card, plan);
}
async function runBatchAiTasks() {
  const settings = await configuredAi();
  if (!settings || processing || deleting) return;
  if (!state.taskFolders.length) return toast('请先在素材页创建 AI 混剪任务文件夹');
  const folders = JSON.parse(JSON.stringify(state.taskFolders));
  const start = $('ai-batch-export');
  const summary = $('batch-summary');
  processing = true;
  pages.mix = 1;
  mixSelection.clear();
  updateTaskControls();
  start.textContent = '✦ 批量任务处理中…';
  let completed = 0, failed = 0;
  try {
    for (const [index, folder] of folders.entries()) {
      summary.textContent = `正在处理 ${index+1}/${folders.length}：${folder.name}`;
      const card = makeResultCard(folder.name);
      try {
        await createMix(folder, settings, card);
        completed++;
      } catch (error) {
        failed++;
        setResultStatus(card, `失败：${error.message}`, 'failed');
      }
    }
    summary.textContent = `批量处理结束：${completed} 个成功，${failed} 个失败。`;
  } finally {
    processing = false;
    start.textContent = '✦ 启动批量 AI 混剪';
    updateTaskControls();
  }
}
async function reviseMix(job, input) {
  const instruction = input.value.trim();
  if (!instruction) return toast('请输入修改意见');
  const settings = await configuredAi();
  if (!settings || processing || deleting) return;
  const folder = state.taskFolders.find(f => f.id === job.task_folder_id);
  if (!folder) return toast('找不到原任务文件夹');
  processing = true;
  pages.mix = 1;
  mixSelection.clear();
  updateTaskControls();
  const card = makeResultCard(`${folder.name} · 修改中`);
  try {
    await createMix(folder, settings, card, {jobId:job.id, instruction});
    input.value = '';
    toast('修改版已生成，可播放或下载');
  } catch (error) {
    setResultStatus(card, `修改失败：${error.message}`, 'failed');
    toast('修改失败，原成片仍可播放和下载');
  } finally {
    processing = false;
    updateTaskControls();
  }
}
const AI_SETTING_INPUTS={
  base_url:'api-url',model:'api-model',api_key:'api-key',
  audio_model:'api-audio-model',audio_base_url:'api-audio-url',audio_api_key:'api-audio-key',
  tts_model:'api-tts-model',tts_voice:'api-tts-voice',tts_base_url:'api-tts-url',tts_api_key:'api-tts-key'
};
let aiSettingsLoadMessage='';
const desktopAiSettings=globalThis.VideoFusionDesktop;
$('ai-storage-hint').textContent=desktopAiSettings
  ? '保存设置后会用系统加密存储，重启后自动恢复。'
  : '浏览器模式下，设置仅在当前页面有效。';
const aiSettingsReady=(async()=>{
  if(!desktopAiSettings)return;
  try{
    const result=await desktopAiSettings.loadAiSettings();
    if(result.status==='ok')state.ai={...state.ai,...result.settings};
    else if(result.status!=='empty')aiSettingsLoadMessage='无法读取上次的 AI 设置；请检查系统加密存储是否可用，再重新保存。';
  }catch{
    aiSettingsLoadMessage='无法读取上次的 AI 设置，请重新保存。';
  }
})();
function aiServiceOrigin(value,label){
  try{
    const url=new URL(value);
    const localHttp=url.protocol==='http:'&&['127.0.0.1','localhost','[::1]'].includes(url.hostname);
    if((url.protocol==='https:'||localHttp)&&!url.username&&!url.password&&!url.search&&!url.hash)return url.origin;
  }catch{}
  throw new Error(`${label}需要有效的 HTTPS 地址；本机服务可用 HTTP。`);
}
function showAiSettingsError(message=''){
  const error=$('settings-error');
  error.textContent=message;error.classList.toggle('hidden',!message);
}
async function openSettings(){
  await aiSettingsReady;
  if($('settings-dialog').open)return;
  for(const [key,id] of Object.entries(AI_SETTING_INPUTS))$(id).value=state.ai[key]||'';
  $('ai-audio-connections').open=Boolean(state.ai.audio_base_url||state.ai.audio_api_key||state.ai.tts_base_url||state.ai.tts_api_key);
  showAiSettingsError(aiSettingsLoadMessage);
  $('settings-dialog').showModal();
}
$('settings-button').onclick=openSettings;$('ai-settings-link').onclick=openSettings;$('settings-cancel').onclick=()=>$('settings-dialog').close();
$('settings-save').onclick=async()=>{
  const settings=Object.fromEntries(Object.entries(AI_SETTING_INPUTS).map(([key,id])=>[key,$(id).value.trim()]));
  try{
    const primaryOrigin=settings.base_url&&settings.base_url!=='local://builtin'?aiServiceOrigin(settings.base_url,'API Base URL'):'';
    for(const [service,label] of [['audio','音频理解'],['tts','配音']]){
      const endpoint=settings[`${service}_base_url`];
      if(endpoint&&aiServiceOrigin(endpoint,`${label} Base URL`)!==primaryOrigin&&!settings[`${service}_api_key`]){
        $('ai-audio-connections').open=true;
        throw new Error(`${label}使用不同的服务地址，请填写该服务的 API Key。`);
      }
    }
  }catch(error){showAiSettingsError(error.message);return;}
  const button=$('settings-save');
  button.disabled=true;
  try{
    if(desktopAiSettings){
      const {status}=await desktopAiSettings.saveAiSettings(settings);
      if(status!=='ok'){
        showAiSettingsError(status==='unavailable'
          ? '系统加密存储不可用。请解锁系统钥匙串或密码库后重试。'
          : '未能保存 AI 设置，请检查系统加密存储或应用数据目录。');
        return;
      }
    }
    aiSettingsLoadMessage='';
    state.ai=settings;
    $('settings-dialog').close();
    toast(desktopAiSettings?'AI 设置已加密保存':'AI 设置已保存至当前会话');
  }catch{
    showAiSettingsError('未能保存 AI 设置，请重试。');
  }finally{button.disabled=false;}
};

for (const tab of document.querySelectorAll('.tab')) {
  tab.addEventListener('click', () => {
    for (const option of document.querySelectorAll('.tab')) {
      option.classList.toggle('active', option === tab);
      option.setAttribute('aria-pressed', String(option === tab));
    }
    for (const name of ['media','ai']) $(name+'-page').classList.toggle('hidden', tab.dataset.tab !== name);
  });
}
$('task-folder-create').onclick = () => openTaskFolderDialog();
$('task-folder-cancel').onclick = () => $('task-folder-dialog').close();
$('task-folder-save').onclick = () => void saveTaskFolder();
$('ai-batch-export').onclick = () => void runBatchAiTasks();
$('media-select-page').onchange = () => togglePageSelection('media');
$('mix-select-page').onchange = () => togglePageSelection('mix');
$('media-delete-selected').onclick = () => void deleteSelected('media');
$('mix-delete-selected').onclick = () => void deleteSelected('mix');
$('import-button').onclick = () => $('file-input').click();
$('file-input').addEventListener('change', async event => {
  const files = Array.from(event.target.files);
  event.target.value = '';
  if (!files.length) return;
  const importButton = $('import-button');
  importButton.disabled = true;
  try {
    for (const file of files) {
      try {
        toast(`正在导入 ${file.name}…`);
        const media = await request(`/api/media?name=${encodeURIComponent(file.name)}`, {
          method:'POST', headers:{'Content-Type':'application/octet-stream'}, body:file
        });
        state.media.push(media);
        renderMedia();
      } catch (error) {
        toast(`导入 ${file.name} 失败：${error.message}`);
      }
    }
  } finally {
    importButton.disabled = false;
  }
});
(async () => {
  const tasks = [
    ['素材', loadMedia()],
    ['任务文件夹', loadTaskFolders()],
    ['成片记录', loadCompleted()]
  ];
  for (const [index, result] of (await Promise.allSettled(tasks.map(([, task]) => task))).entries()) {
    if (result.status === 'rejected') toast(`无法读取${tasks[index][0]}：${result.reason.message}`);
  }
})();
