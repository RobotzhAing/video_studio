'use strict';
const assert = require('node:assert/strict');
const {createCipheriv, createDecipheriv, randomBytes} = require('node:crypto');
const {mkdtemp, readFile, readdir, rm, writeFile} = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {test} = require('node:test');
const {createAiSettingsStore, registerAiSettingsHandlers} = require('../desktop/ai-settings.cjs');

function fixtureEncryption() {
  const key = randomBytes(32);
  return {
    getSelectedStorageBackend: () => 'gnome_libsecret',
    isEncryptionAvailable: () => true,
    encryptString(text) {
      const nonce = randomBytes(12);
      const cipher = createCipheriv('aes-256-gcm', key, nonce);
      const data = Buffer.concat([cipher.update(text, 'utf8'), cipher.final()]);
      return Buffer.concat([nonce, cipher.getAuthTag(), data]);
    },
    decryptString(blob) {
      const decipher = createDecipheriv('aes-256-gcm', key, blob.subarray(0, 12));
      decipher.setAuthTag(blob.subarray(12, 28));
      return Buffer.concat([decipher.update(blob.subarray(28)), decipher.final()]).toString('utf8');
    },
  };
}

test('AI settings survive a new store instance and never write plaintext to disk', async t => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'vf-settings-'));
  t.after(() => rm(directory, {recursive: true, force: true}));
  const storage = fixtureEncryption();
  const first = createAiSettingsStore(directory, storage);
  assert.deepEqual(await first.load(), {status: 'empty'});
  const secret = 'fake-secret-settings-persistence-test';
  assert.deepEqual(await first.save({base_url: 'https://provider.test', model: 'test-model',
    api_key: secret, audio_api_key: secret + '-audio', tts_api_key: secret + '-tts'}), {status: 'ok'});
  const names = await readdir(directory);
  assert.deepEqual(names, ['ai-settings.v1.enc']);
  assert.ok(!(await readFile(path.join(directory, names[0]))).includes(secret));
  const next = createAiSettingsStore(directory, storage);
  const loaded = await next.load();
  assert.equal(loaded.status, 'ok');
  assert.equal(loaded.settings.base_url, 'https://provider.test');
  assert.equal(loaded.settings.model, 'test-model');
  assert.equal(loaded.settings.api_key, secret);
  assert.equal(loaded.settings.audio_api_key, secret + '-audio');
  assert.equal(loaded.settings.tts_api_key, secret + '-tts');
  assert.deepEqual(await next.save({api_key: ''}), {status: 'ok'});
  assert.equal((await first.load()).settings.api_key, '');
  await writeFile(path.join(directory, names[0]), 'corrupt data');
  assert.deepEqual(await next.load(), {status: 'read-error'});
});

test('AI settings refuse to persist secrets without system encryption', async t => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'vf-settings-'));
  t.after(() => rm(directory, {recursive: true, force: true}));
  const settings = createAiSettingsStore(directory, {isEncryptionAvailable: () => false});
  assert.deepEqual(await settings.save({api_key: 'never-written'}), {status: 'unavailable'});
  assert.deepEqual(await readdir(directory), []);
});

test('AI settings IPC only accepts the registered main frame at its exact app URL', () => {
  const handlers = new Map();
  const ipcMain = {handle: (channel, handler) => handlers.set(channel, handler)};
  const pageUrl = 'http://127.0.0.1:4321/';
  const webContents = {mainFrame: {url: pageUrl}};
  const store = {load: () => ({status: 'ok'}), save: settings => settings};
  registerAiSettingsHandlers(ipcMain, {webContents, pageUrl, store});
  const invokeLoad = handlers.get('videofusion:ai-settings:load');
  const invokeSave = handlers.get('videofusion:ai-settings:save');
  assert.deepEqual(invokeLoad({sender: webContents, senderFrame: webContents.mainFrame}), {status: 'ok'});
  assert.deepEqual(invokeSave({sender: webContents, senderFrame: webContents.mainFrame}, {model: 'test'}), {model: 'test'});
  assert.deepEqual(invokeLoad({sender: webContents, senderFrame: {url: pageUrl}}), {status: 'denied'});
  assert.deepEqual(invokeLoad({sender: {}, senderFrame: webContents.mainFrame}), {status: 'denied'});
  webContents.mainFrame.url += 'other';
  assert.deepEqual(invokeSave({sender: webContents, senderFrame: webContents.mainFrame}, {}), {status: 'denied'});
});
