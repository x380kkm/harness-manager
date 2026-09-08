// audience: internal
// # snapshot-command
// 命令从真实管理核心导出本机观察, 界面文件可在普通浏览器中读取该快照.

import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { ManagerProcess } from './rpc.mjs';
import { exportSnapshot } from './snapshot-export.mjs';

// //// 连接默认用户范围并导出本机快照 [@x380kkm 2026-09-06] ////
const manager = new ManagerProcess({ projectDirectory: fileURLToPath(new URL('../../', import.meta.url)), spawnProcess: spawn });
try {
  manager.start(null, []);
  const result = await exportSnapshot(manager, fileURLToPath(new URL('../renderer/', import.meta.url)));
  console.log(`本机观察: ${result.count} 项\n快照: ${result.path}`);
} finally {
  await manager.close();
}
// //// /连接默认用户范围并导出本机快照 ////
