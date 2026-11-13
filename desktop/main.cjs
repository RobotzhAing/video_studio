const { app, BrowserWindow, dialog, ipcMain, safeStorage, shell } = require('electron');
const { spawn } = require('node:child_process');
const net = require('node:net');
const http = require('node:http');
const path = require('node:path');
const {createAiSettingsStore, registerAiSettingsHandlers} = require('./ai-settings.cjs');

let backend;
let window;
let shuttingDown = false;
let logs = '';

function freePort() {
  return new Promise((resolve, reject) => {
    const listener = net.createServer();
    listener.once('error', reject);
    listener.listen(0, '127.0.0.1', () => {
      const port = listener.address().port;
      listener.close(err => err ? reject(err) : resolve(port));
    });
  });
}

function ready(port, deadline) {
  return new Promise((resolve, reject) => {
    function poll() {
      if (!backend || backend.exitCode !== null || backend.signalCode !== null) return reject(new Error(`视频服务已退出。\n${logs.slice(-2000)}`));
      const req = http.get({host: '127.0.0.1', port, path: '/api/health', timeout: 1000}, res => {
        res.resume();
        if (res.statusCode === 200) resolve();
        else retry();
      });
      req.on('error', retry);
      req.on('timeout', () => req.destroy());
    }
    function retry() {
      if (Date.now() >= deadline) reject(new Error(`视频服务启动超时。\n${logs.slice(-2000)}`));
      else setTimeout(poll, 200);
    }
    poll();
  });
}

async function start() {
  const port = await freePort();
  const devRoot = path.join(__dirname, '..');
  const web = app.isPackaged ? path.join(process.resourcesPath, 'web') : path.join(devRoot, 'web');
  const exe = process.platform === 'win32' ? '.exe' : '';
  const command = app.isPackaged ? path.join(process.resourcesPath, 'runtime', 'VideoFusionBackend' + exe)
    : process.env.VIDEOSTUDIO_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  const args = app.isPackaged ? [] : [path.join(devRoot, 'server', 'main.py')];
  args.push('--port', String(port), '--web-dir', web);
  const env = {...process.env, VIDEOSTUDIO_DATA_DIR: app.getPath('userData')};
  if (app.isPackaged) {
    env.VIDEOSTUDIO_FFMPEG = path.join(process.resourcesPath, 'runtime', 'ffmpeg' + exe);
    env.VIDEOSTUDIO_FFPROBE = path.join(process.resourcesPath, 'runtime', 'ffprobe' + exe);
  }
  backend = spawn(command, args, {env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe']});
  backend.on('error', err => { logs += String(err); });
  for (const stream of [backend.stdout, backend.stderr]) stream.on('data', chunk => { logs = (logs + chunk.toString()).slice(-8000); });
  backend.once('exit', () => {
    if (!shuttingDown && window && !window.isDestroyed()) dialog.showErrorBox('视频服务已退出', logs.slice(-2000) || '请重新启动应用。');
  });
  await ready(port, Date.now() + 45000);
  window = new BrowserWindow({width: 1500, height: 960, minWidth: 1040, minHeight: 680, show: false, backgroundColor: '#111217', title: 'VideoFusion Studio', webPreferences: {preload: path.join(__dirname, 'preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true}});
  const origin = `http://127.0.0.1:${port}`;
  window.webContents.on('will-navigate', (event, url) => { if (!url.startsWith(origin + '/')) event.preventDefault(); });
  window.webContents.setWindowOpenHandler(({url}) => { if (/^https?:/.test(url)) shell.openExternal(url); return {action:'deny'}; });
  const pageUrl = origin + '/';
  registerAiSettingsHandlers(ipcMain, {webContents: window.webContents, pageUrl,
    store: createAiSettingsStore(app.getPath('userData'), safeStorage)});
  await window.loadURL(pageUrl);
  window.show();
}

app.whenReady().then(start).catch(err => { dialog.showErrorBox('启动失败', String(err)); app.quit(); });
app.on('window-all-closed', () => app.quit());
app.on('before-quit', () => { shuttingDown = true; if (backend && backend.exitCode === null) backend.kill(); });
