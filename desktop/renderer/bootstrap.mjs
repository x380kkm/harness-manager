// audience: internal
// # manager-bootstrap
// 桌面使用预加载接口, 普通浏览器只读取本地导出快照.

import { start } from './ui.mjs';

// //// 给静态页面提供固定快照的只读接口 [@x380kkm 2026-09-06] ////
export function snapshotBridge(snapshot) {
  const readOnlyError = { ok: false, error: { code: 'snapshot-read-only', message: '请在桌面应用中读取当前磁盘或保存登记.' } };
  return Object.freeze({
    readOnly: true,
    getContext: async () => ({ ok: true, result: { userRoot: snapshot.userRoot, workspace: null } }),
    setDirty: () => {},
    call: async (method) => ['codex.snapshot', 'card.inventory'].includes(method) ? { ok: true, result: snapshot }
      : method === 'statistics.reads' && snapshot.statistics ? { ok: true, result: snapshot.statistics }
        : method === 'host.status' && snapshot.hostControl ? { ok: true, result: snapshot.hostControl } : readOnlyError,
  });
}

// //// 打开可用工作区并保留可见的启动反馈 [@x380kkm 2026-09-06] ////
function boot() {
  try {
    if (!window.manager) {
      if (!window.harnessLocalSnapshot?.items) throw new Error('此目录还没有本机快照. 运行启动管理器.ps1, 或在 desktop 目录执行 npm run snapshot.');
      window.manager = snapshotBridge(window.harnessLocalSnapshot);
    }
    start();
    document.getElementById('shell').hidden = false;
    document.getElementById('startup-message').hidden = true;
  } catch (error) {
    document.getElementById('startup-error').textContent = error.message || String(error);
  }
}

boot();
