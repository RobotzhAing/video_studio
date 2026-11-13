const fs = require('node:fs');

// Inspect executable headers directly: running an x86 binary under Rosetta
// would otherwise hide a packaging error on Apple Silicon.
function architectures(file) {
  const fd = fs.openSync(file, 'r');
  const b = Buffer.alloc(65536);
  let n;
  try { n = fs.readSync(fd, b, 0, b.length, 0); } finally { fs.closeSync(fd); }
  if (n < 64) throw new Error('可执行文件头过短');
  const machoCpu = cpu => ({0x0100000c: 'arm64', 0x01000007: 'x64', 7: 'ia32', 12: 'arm'}[cpu]);
  const bigMagic = b.readUInt32BE(0);
  const littleMagic = b.readUInt32LE(0);
  if ([0xfeedface, 0xfeedfacf].includes(littleMagic)) return [machoCpu(b.readUInt32LE(4))].filter(Boolean);
  if ([0xfeedface, 0xfeedfacf].includes(bigMagic)) return [machoCpu(b.readUInt32BE(4))].filter(Boolean);
  if ([0xcafebabe, 0xcafebabf].includes(bigMagic)) {
    const count = b.readUInt32BE(4), stride = bigMagic === 0xcafebabf ? 32 : 20;
    if (count > 64 || 8 + count * stride > n) throw new Error('Mach-O 通用二进制头无效');
    return Array.from({length: count}, (_, i) => machoCpu(b.readUInt32BE(8 + i * stride))).filter(Boolean);
  }
  if (b[0] === 0x4d && b[1] === 0x5a) {
    const pe = b.readUInt32LE(0x3c);
    if (pe + 6 > n || b.toString('binary', pe, pe + 4) !== 'PE\0\0') throw new Error('PE 文件头无效');
    return [({0x8664: 'x64', 0xaa64: 'arm64', 0x014c: 'ia32', 0x01c4: 'arm'}[b.readUInt16LE(pe + 4)])].filter(Boolean);
  }
  if (b[0] === 0x7f && b.toString('ascii', 1, 4) === 'ELF') {
    const machine = b[5] === 2 ? b.readUInt16BE(18) : b.readUInt16LE(18);
    return [({62: 'x64', 183: 'arm64', 3: 'ia32', 40: 'arm'}[machine])].filter(Boolean);
  }
  throw new Error('无法识别可执行文件格式');
}

function assertArchitecture(file, expected = process.arch) {
  const found = architectures(file);
  if (!found.includes(expected)) throw new Error(`可执行文件架构不匹配：${file}；需要 ${expected}，实际 ${found.join(', ') || '未知'}`);
}

module.exports = {architectures, assertArchitecture};
