'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const BatchTasks = require('../web/batch-tasks.js');

test('batch payload targets 1080p and preserves supported source frame rate', () => {
  const asset = {id: 'video-a', type: 'video', duration: 4, width: 1920, height: 1080,
    fps_num: 30000, fps_den: 1001};
  const payload = BatchTasks.createRenderPayload({name: 'Trip'}, [asset], {
    clips: [{id: 'video-a', start: 0.5, end: 2.5, speed: 1}],
    textClips: [{text: 'Trip title', start: 0, end: 1}]
  });

  assert.equal(payload.width, 1920);
  assert.equal(payload.height, 1080);
  assert.deepEqual(payload.frameRate, {numerator: 30000, denominator: 1001});
  assert.equal(payload.outputName, 'Trip');
  assert.equal(payload.clips[0].timelineStartTick, 0);
  assert.equal(payload.clips[0].end, 2.5);
  assert.equal(payload.textClips[0].durationTicks, 240000);
});

test('batch payload selects portrait 1080p and lays clips consecutively', () => {
  const assets = [
    {id: 'one', type: 'video', duration: 3, width: 720, height: 1280, fps_num: 29, fps_den: 1},
    {id: 'two', type: 'image', duration: 5, width: 1000, height: 1500}
  ];
  const payload = BatchTasks.createRenderPayload({name: 'Vertical'}, assets, {
    clips: [{id: 'one', start: 0, end: 2, speed: 1}, {id: 'two', start: 1, end: 3, speed: 2}],
    textClips: []
  });

  assert.equal(payload.width, 1080);
  assert.equal(payload.height, 1920);
  assert.deepEqual(payload.frameRate, {numerator: 30, denominator: 1});
  assert.equal(payload.clips[0].timelineStartTick, 0);
  assert.equal(payload.clips[1].timelineStartTick, 480000);
});

test('batch payload rejects plans referencing media outside their folder', () => {
  assert.throws(() => BatchTasks.createRenderPayload({name: 'X'}, [
    {id: 'inside', type: 'video', duration: 2}
  ], {clips: [{id: 'outside', start: 0, end: 1}], textClips: []}), /文件夹外/);
});

test('complete batch exports lay generated narration, music and captions on the same timeline', () => {
  const asset = {id: 'source', type: 'video', duration: 6};
  const narrationId = 'a'.repeat(32), musicId = 'b'.repeat(32);
  const payload = BatchTasks.createRenderPayload({name: 'Mix'}, [asset], {
    clips: [{id: 'source', start: 0, end: 6, speed: 2}],
    outputCanvas: {width: 1080, height: 1920},
    audioMix: {narrationId, musicId, narrationDuration: 3.01},
    textClips: [{text: 'Bon voyage !', start: 0.5, end: 3}]
  }, {completeMix: true});

  assert.equal(payload.completeMix, true);
  assert.deepEqual([payload.width, payload.height], [1080, 1920]);
  assert.deepEqual(payload.music, {id: musicId, volume: 0.17});
  assert.equal(payload.audioClips[0].id, narrationId);
  assert.equal(payload.audioClips[0].end, 3);
  assert.equal(payload.audioClips[0].timelineStartTick, 0);
  assert.ok(payload.tracks.some(track => track.kind === 'audio' && track.trackId === payload.audioClips[0].trackId));
  assert.equal(payload.textClips[0].text, 'Bon voyage !');
  assert.equal(payload.textClips[0].timelineStartTick + payload.textClips[0].durationTicks, 3 * 240000);
});

test('complete batch refuses missing audio or subtitles containing Han characters', () => {
  const asset = {id: 'source', type: 'video', duration: 4};
  const folder = {name: 'Mix', reference_video_id: 'b'.repeat(32)};
  const make = plan => BatchTasks.createRenderPayload(folder, [asset], {
    clips: [{id: 'source', start: 0, end: 4}], outputCanvas: {width: 1920, height: 1080}, ...plan
  }, {completeMix: true});

  assert.throws(() => make({}), /没有生成字幕/);
  assert.throws(() => make({textClips: [{text: 'Run! 漢', start: 0, end: 1}]}), /含有汉字/);
  assert.throws(() => make({textClips: [{text: 'Run!', start: 0, end: 1}]}), /没有生成有效的配音和背景音乐/);
  assert.throws(() => make({textClips: [{text: 'Run!', start: 0, end: 1}],
    audioMix: {narrationId: 'a'.repeat(32), musicId: 'b'.repeat(32), narrationDuration: 2}}), /背景音乐/);
});


test('portrait batch captions wrap long text within side margins', () => {
  const asset = {id: 'source', type: 'video', duration: 4};
  const caption = 'Watch this marshmallow squishy bounce back into shape.';
  const payload = BatchTasks.createRenderPayload({name: 'Portrait'}, [asset], {
    clips: [{id: 'source', start: 0, end: 4}],
    outputCanvas: {width: 1080, height: 1920},
    textClips: [{text: caption, start: 0, end: 3}],
    audioMix: {narrationId: 'a'.repeat(32), musicId: 'b'.repeat(32), narrationDuration: 3}
  }, {completeMix: true});
  assert.equal(payload.textClips[0].fontSize, 36);
  assert.match(payload.textClips[0].text, /\n/);
  assert.equal(payload.textClips[0].text.replace(/\n/g, ' '), caption);
});
