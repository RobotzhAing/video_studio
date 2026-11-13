const {spawnSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const {assertArchitecture} = require('./check-binary-architecture.cjs');
const root = path.join(__dirname, '..');
const runtime = path.join(root, 'build', 'runtime');
fs.mkdirSync(runtime, {recursive: true});
const python = process.env.VIDEOSTUDIO_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const result = spawnSync(python, ['-m', 'PyInstaller', '--noconfirm', '--onefile', '--name', 'VideoFusionBackend', '--distpath', runtime, '--workpath', path.join(root, 'build', 'pyinstaller'), '--specpath', path.join(root, 'build'), path.join(root, 'server', 'main.py')], {stdio: 'inherit', cwd: root});
if (result.error || result.status !== 0) {
  console.error('构建后端失败。请先用 Python 安装 PyInstaller。', result.error || '');
  process.exit(result.status || 1);
}
console.log('Built AI-mix-only backend runtime; matting model is intentionally omitted.');
const suffix = process.platform === 'win32' ? '.exe' : '';
for (const [source, name] of [[require('ffmpeg-static'), 'ffmpeg'], [process.env.VIDEOFUSION_FFPROBE_BUILD || require('ffprobe-static').path, 'ffprobe']]) {
  if (!source || !fs.existsSync(source)) throw new Error(`缺少 ${name} 的当前平台二进制，请重新安装 npm 依赖。`);
  assertArchitecture(source);
  const dest = path.join(runtime, name + suffix);
  fs.copyFileSync(source, dest);
  if (process.platform !== 'win32') fs.chmodSync(dest, 0o755);
  console.log(`Bundled ${name}: ${dest}`);
}

const notices = path.join(runtime, 'third-party');
fs.mkdirSync(notices, {recursive: true});
const groups = [
  ['ffmpeg-static', ['LICENSE', 'ffmpeg.LICENSE', 'ffmpeg.README']],
  ['ffprobe-static', ['LICENSE', 'LICENSE.GPLv3', 'THIRD_PARTY_NOTICES.md',
    ...['BUILD_INFO.md', 'LICENSE.LGPLv2.1', 'LICENSE.LGPLv3'].map(name =>
      path.join('bin', process.platform, process.arch, name))]],
];
for (const [dependency, files] of groups) {
  if (dependency === 'ffprobe-static' && process.env.VIDEOFUSION_FFPROBE_BUILD) continue;
  const dependencyDir = path.dirname(require.resolve(dependency + '/package.json'));
  for (const file of files) {
    const source = path.join(dependencyDir, file);
    if (fs.existsSync(source)) fs.copyFileSync(source, path.join(notices, dependency + '-' + path.basename(file)));
  }
}
if (process.env.VIDEOFUSION_FFPROBE_BUILD) {
  const note = 'Bundled ffmpeg and ffprobe originate from the matching FFmpeg 6.0 platform builds.\n' +
    'Binary releases, build scripts, and source references: https://github.com/eugeneware/ffmpeg-static/tree/b6.0\n' +
    'FFmpeg source: https://ffmpeg.org/releases/ffmpeg-6.0.tar.xz\n';
  fs.writeFileSync(path.join(notices, 'ffmpeg-source.txt'), note);
}
