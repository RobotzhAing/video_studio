'use strict';

(function attachBatchTasks(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.VideoFusionBatchTasks = api;
})(typeof globalThis === 'object' ? globalThis : this, function createBatchTasks() {
  const TIMEBASE = 240000;
  const SUPPORTED_RATES = new Set(['24/1', '25/1', '30/1', '50/1', '60/1',
    '24000/1001', '30000/1001', '60000/1001']);

  function frameRateFor(media) {
    const numerator = Number(media?.fps_num);
    const denominator = Number(media?.fps_den);
    const candidate = `${numerator}/${denominator}`;
    if (Number.isInteger(numerator) && Number.isInteger(denominator) && denominator > 0 &&
        SUPPORTED_RATES.has(candidate)) {
      return {numerator, denominator};
    }
    return {numerator: 30, denominator: 1};
  }

  function canvasFor(media) {
    const width = Number(media?.width) || 0;
    const height = Number(media?.height) || 0;
    if (width > 0 && height > width) return {width: 1080, height: 1920};
    return {width: 1920, height: 1080};
  }

  function wrapCaption(text, budget) {
    const weight = ch => /[\u0000-\u007f]/.test(ch) ? (/[MW@%]/.test(ch) ? 1 : 0.65) : 1;
    const lines = [];
    let line = '', used = 0;
    const flush = () => { if (line) lines.push(line.trim()); line = ''; used = 0; };
    for (const word of text.trim().split(/\s+/u)) {
      const characters = Array.from(word);
      const size = characters.reduce((n, ch) => n + weight(ch), 0);
      if (line && used + 0.65 + size > budget) flush();
      if (line) { line += ' '; used += 0.65; }
      for (const ch of characters) {
        const next = weight(ch);
        if (used + next > budget) flush();
        line += ch; used += next;
      }
    }
    flush();
    return lines.join('\n');
  }

  function createRenderPayload(folder, selectedMedia, plan, {completeMix = false} = {}) {
    if (!folder || typeof folder.name !== 'string' || !folder.name.trim()) {
      throw new Error('任务文件夹名称不能为空');
    }
    if (!Array.isArray(selectedMedia) || !selectedMedia.length) {
      throw new Error('任务文件夹中没有可用的视频或图片素材');
    }
    if (!plan || !Array.isArray(plan.clips) || !plan.clips.length || plan.clips.length > 100) {
      throw new Error('AI 没有生成有效的视频片段');
    }
    const byId = new Map(selectedMedia.map(asset => [asset.id, asset]));
    const lead = selectedMedia[0];
    const canvas = completeMix ? plan.outputCanvas : canvasFor(lead);
    if (!canvas || ![canvas.width, canvas.height].every(n => Number.isInteger(n) && n >= 64 && n <= 7680 && n % 2 === 0)) {
      throw new Error('未收到有效的成片画幅，已停止导出');
    }
    let cursorTicks = 0;
    const clips = plan.clips.map((planned, index) => {
      const asset = byId.get(planned?.id);
      const start = Number(planned?.start);
      const end = Number(planned?.end);
      const speed = Number(planned?.speed) || 1;
      if (!asset || !['video', 'image'].includes(asset.type) || !Number.isFinite(start) ||
          !Number.isFinite(end) || !(start >= 0 && end > start && end <= Number(asset.duration) + 0.05) ||
          !Number.isFinite(speed) || speed < 0.25 || speed > 4) {
        throw new Error(`AI 方案第 ${index + 1} 个片段无效或引用了文件夹外的素材`);
      }
      const timelineStartTick = cursorTicks;
      const durationTicks = Math.round(((Math.min(end, Number(asset.duration)) - start) / speed) * TIMEBASE);
      if (durationTicks < 12000) throw new Error(`AI 方案第 ${index + 1} 个片段短于 0.05 秒`);
      cursorTicks += durationTicks;
      return {id: asset.id, start, end: Math.min(end, Number(asset.duration)), speed,
        trackId: 'video-main', clipId: `batch-clip-${index + 1}`, timelineStartTick,
        ...(completeMix ? {timelineEndTick: cursorTicks} : {})};
    });
    const tracks = [{trackId: 'video-main', kind: 'video', order: 0}];
    const titleCues = Array.isArray(plan.textClips) ? plan.textClips : [];
    if (completeMix && !titleCues.length) throw new Error('AI 没有生成字幕，已停止导出');
    const textClips = titleCues.map((title, index) => {
      const label = completeMix ? '字幕' : '标题';
      const start = Number(title?.start);
      const end = Number(title?.end);
      if (typeof title?.text !== 'string' || !title.text.trim() || !Number.isFinite(start) ||
          !Number.isFinite(end) || start < 0 || end <= start || end * TIMEBASE > cursorTicks + 1) {
        throw new Error(`AI 方案第 ${index + 1} 个${label}内容或时间范围无效`);
      }
      if (completeMix && /\p{Script=Han}/u.test(title.text)) {
        throw new Error(`AI 方案第 ${index + 1} 条字幕含有汉字，已停止导出`);
      }
      const timelineStartTick = Math.round(start * TIMEBASE);
      // Leave side margins on narrow outputs. Split long words as a last
      // resort so unspaced scripts cannot extend beyond the canvas.
      const fontSize = completeMix && canvas.width < canvas.height ? 36 : completeMix ? 48 : 64;
      const lineBudget = Math.max(12, Math.floor(canvas.width * 0.86 / fontSize));
      const wrapped = completeMix ? wrapCaption(title.text, lineBudget) : title.text;
      const durationTicks = Math.min(cursorTicks, Math.round(end * TIMEBASE)) - timelineStartTick;
      if (durationTicks < 12000) throw new Error(`AI 方案第 ${index + 1} 个${label}短于 0.05 秒`);
      return {clipId: `batch-${completeMix ? 'subtitle' : 'title'}-${index + 1}`,
        trackId: 'video-main', text: wrapped,
        timelineStartTick, durationTicks,
        fontSize, x: 50, y: completeMix ? 87 : 84,
        color: '#ffffff', outlineColor: '#000000', outlineWidth: completeMix ? 3 : 2, align: 'center'};
    });
    const payload = {clips, tracks,
      width: canvas.width, height: canvas.height, frameRate: frameRateFor(lead), textClips,
      outputName: folder.name.trim()};
    if (completeMix) {
      const {narrationId, narrationDuration, musicId} = plan.audioMix || {};
      const validAssetId = id => typeof id === 'string' && /^[0-9a-f]{32}$/.test(id) &&
        !byId.has(id) && id !== folder.reference_video_id;
      if (!validAssetId(narrationId) || !validAssetId(musicId) || narrationId === musicId ||
          !Number.isFinite(narrationDuration) || narrationDuration < 0.05) {
        throw new Error('AI 没有生成有效的配音和背景音乐，已停止导出');
      }
      tracks.push({trackId: 'audio-narration', kind: 'audio', order: 1});
      Object.assign(payload, {completeMix: true,
        music: {id: musicId, volume: 0.17},
        audioClips: [{id: narrationId, start: 0, end: Math.min(narrationDuration, cursorTicks / TIMEBASE),
          trackId: 'audio-narration', timelineStartTick: 0, volume: 1}]});
    }
    return payload;
  }

  return {frameRateFor, canvasFor, createRenderPayload};
});
