'use strict';
const {contextBridge, ipcRenderer} = require('electron');

contextBridge.exposeInMainWorld('VideoFusionDesktop', {
  loadAiSettings: () => ipcRenderer.invoke('videofusion:ai-settings:load'),
  saveAiSettings: settings => ipcRenderer.invoke('videofusion:ai-settings:save', settings),
});
