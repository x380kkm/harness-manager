// audience: internal
// # codex-snapshot-export
// 导出使用盘点接口的安全摘要. 生成文件保留在当前本机界面目录, 每次导出替换前一次快照.

import { writeFile, rename } from 'node:fs/promises';
import path from 'node:path';

// //// 将本机观察写成浏览器可直接读取的快照 [@x380kkm 2026-09-06] ////
export async function exportSnapshot(manager, directory) {
  const snapshot = await manager.request('card.inventory', {});
  const target = path.join(directory, 'local-inventory.js');
  const temporary = path.join(directory, `.local-inventory-${process.pid}.js`);
  await writeFile(temporary, `globalThis.harnessLocalSnapshot = ${JSON.stringify(snapshot)};\n`, 'utf8');
  await rename(temporary, target);
  return { path: target, count: snapshot.items.length, scannedAt: snapshot.scannedAt };
}
