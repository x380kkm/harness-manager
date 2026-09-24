// audience: internal
// # inventory-share-errors
// 共享错误与随后的读取错误通过卡片事件保留各自的详情.

import assert from 'node:assert/strict';
import test from 'node:test';
import { rendererRuntime } from './renderer-runtime.mjs';
import { createInventoryView } from '../desktop/renderer/inventory-view.mjs';

// //// 打开可共享卡片并在写入后的刷新注入独立错误 [@x380kkm 2026-09-10] ////
async function openSharingControl(context, shareError, refreshError) {
  const calls = [];
  const statuses = [];
  let shareAttempted = false;
  const item = { id: 'rule', kind: 'rule', name: '编码规范', summary: '正文', content: '使用 UTF8 编码.',
    path: '/rules', details: { catalogScope: 'project-local' } };
  const snapshot = { root: '/user', workspace: '/project', scannedAt: new Date().toISOString(), items: [item],
    groups: [{ id: 'group:rule', primaryId: item.id, itemIds: [item.id], category: 'rule', name: item.name,
      origin: 'user', counts: { rule: 1 } }], edges: [], diagnostics: [],
    cardManagement: { rule: { supported: true, scope: 'project-local', userState: 'enabled',
      privateState: 'inherit', effectiveEnabled: true, shared: false } } };
  const get = rendererRuntime(context, (method) => {
    calls.push(method);
    if (method === 'card.inventory') {
      if (shareAttempted && refreshError) throw refreshError;
      return snapshot;
    }
    if (method === 'card.describe') return { sharingBaseline: { source: 'rule' } };
    if (method === 'card.set_shared') { shareAttempted = true; if (shareError) throw shareError; return {}; }
    throw new Error(`Unexpected method ${method}`);
  });
  const view = createInventoryView({ run: (action) => action(), setStatus: (value) => statuses.push(value), onDirty() {}, onProject: async () => '/project' });
  await view.openProject();
  await get('inventory-list').querySelector('.inventory-card-name').emit('click');
  const control = get('inventory-inspector').querySelector('.card-shared input');
  control.checked = true;
  return { control, calls, statuses };
}

// //// 共享恢复资料与刷新失败原因同时保留 [@x380kkm 2026-09-10] ////
test('刷新失败保留共享错误代码和恢复路径', async (context) => {
  const recovery = { path: '/user/sharing-recovery.json' };
  const shareError = Object.assign(new Error('共享恢复需要确认.'), { code: 'sharing_recovery', details: { recovery } });
  const refreshError = Object.assign(new Error('目录读取失败.'), { code: 'inventory_io', details: { path: '/catalog' } });
  const { control, calls } = await openSharingControl(context, shareError, refreshError);
  await assert.rejects(control.emit('change'), (error) => {
    assert.equal(error, shareError);
    assert.equal(error.code, 'sharing_recovery');
    assert.equal(error.message, '共享恢复需要确认.');
    assert.equal(error.details.recovery, recovery);
    assert.deepEqual(error.details.refresh, { message: '目录读取失败.', code: 'inventory_io', details: { path: '/catalog' } });
    return true;
  });
  assert.deepEqual(calls, ['card.inventory', 'card.describe', 'card.set_shared', 'card.inventory']);
});

// //// 刷新成功时沿用共享错误对象与原始详情 [@x380kkm 2026-09-10] ////
test('刷新成功直接传递原共享错误', async (context) => {
  const details = { recovery: '/user/sharing-recovery.json' };
  const shareError = Object.assign(new Error('共享恢复需要确认.'), { code: 'sharing_recovery', details });
  const { control } = await openSharingControl(context, shareError);
  await assert.rejects(control.emit('change'), (error) => {
    assert.equal(error, shareError);
    assert.equal(error.details, details);
    return true;
  });
});

// //// 文本错误详情与普通读取错误保持可见 [@x380kkm 2026-09-10] ////
test('附加刷新错误保留文本形式的共享详情', async (context) => {
  const shareError = Object.assign(new Error('共享失败.'), { code: 'sharing_recovery', details: '/user/recovery.json' });
  const { control } = await openSharingControl(context, shareError, new Error('连接中断.'));
  await assert.rejects(control.emit('change'), (error) => {
    assert.equal(error, shareError);
    assert.equal(error.details.cause, '/user/recovery.json');
    assert.equal(error.details.refresh.message, '连接中断.');
    return true;
  });
});

// //// 保存成功后的读取失败保持明确的保存结果 [@x380kkm 2026-09-10] ////
test('共享成功后刷新失败仍显示已保存', async (context) => {
  const { control, statuses } = await openSharingControl(context, null, new Error('连接中断.'));
  await control.emit('change');
  assert.match(statuses.at(-1), /项目共享文件已更新/);
  assert.match(statuses.at(-1), /刷新目录失败: 连接中断/);
});
