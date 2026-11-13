'use strict';

const fs = require('node:fs/promises');
const path = require('node:path');
const {randomUUID} = require('node:crypto');

const CHANNELS = {
  load: 'videofusion:ai-settings:load',
  save: 'videofusion:ai-settings:save',
};
const FIELD_LIMITS = {
  base_url: 2048, model: 500, api_key: 5000, audio_model: 160,
  audio_base_url: 2048, audio_api_key: 5000, tts_model: 160,
  tts_voice: 160, tts_base_url: 2048, tts_api_key: 5000,
};
const MAX_FILE_BYTES = 128 * 1024;

function normalizeSettings(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const settings = {};
  for (const [key, limit] of Object.entries(FIELD_LIMITS)) {
    const entry = Object.hasOwn(value, key) ? value[key] : '';
    if (typeof entry !== 'string' || entry.length > limit) return null;
    settings[key] = entry;
  }
  return settings;
}

function encryptedStorageAvailable(safeStorage) {
  return safeStorage.isEncryptionAvailable() &&
    (process.platform !== 'linux' || safeStorage.getSelectedStorageBackend() !== 'basic_text');
}

function createAiSettingsStore(directory, safeStorage) {
  const file = path.join(directory, 'ai-settings.v1.enc');
  return {
    async load() {
      try {
        const {size} = await fs.stat(file);
        if (size === 0 || size > MAX_FILE_BYTES) return {status: 'read-error'};
        if (!encryptedStorageAvailable(safeStorage)) return {status: 'unavailable'};
        const blob = await fs.readFile(file);
        const settings = normalizeSettings(JSON.parse(safeStorage.decryptString(blob)));
        return settings ? {status: 'ok', settings} : {status: 'read-error'};
      } catch (err) {
        return {status: err?.code === 'ENOENT' ? 'empty' : 'read-error'};
      }
    },
    async save(input) {
      const settings = normalizeSettings(input);
      if (!settings) return {status: 'invalid'};
      const temp = file + '.' + randomUUID() + '.tmp';
      try {
        if (!encryptedStorageAvailable(safeStorage)) return {status: 'unavailable'};
        const blob = safeStorage.encryptString(JSON.stringify(settings));
        if (!blob.length || blob.length > MAX_FILE_BYTES) return {status: 'invalid'};
        await fs.mkdir(directory, {recursive: true, mode: 0o700});
        await fs.writeFile(temp, blob, {flag: 'wx', mode: 0o600});
        await fs.rename(temp, file);
        return {status: 'ok'};
      } catch {
        await fs.unlink(temp).catch(() => {});
        return {status: 'write-error'};
      }
    },
  };
}

function registerAiSettingsHandlers(ipcMain, {webContents, pageUrl, store}) {
  const isAllowed = event => event.sender === webContents &&
    event.senderFrame === webContents.mainFrame &&
    event.senderFrame?.url === pageUrl;
  ipcMain.handle(CHANNELS.load, event => isAllowed(event) ? store.load() : {status: 'denied'});
  ipcMain.handle(CHANNELS.save, (event, value) => isAllowed(event) ? store.save(value) : {status: 'denied'});
}

module.exports = {createAiSettingsStore, registerAiSettingsHandlers};
