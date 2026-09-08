// audience: internal
// # editor-recovery

import assert from 'node:assert/strict';
import test from 'node:test';
import { rendererRuntime } from './renderer-runtime.mjs';
import { createHookEditor } from '../desktop/renderer/hook-editor.mjs';
import { createCompanionDialog } from '../desktop/renderer/companion-dialog.mjs';
import { createRelationsDialog } from '../desktop/renderer/relations-dialog.mjs';
import { createUsageDialog } from '../desktop/renderer/usage-dialog.mjs';

// //// Hook 表单保留可选元数据与处理项的字段 [@x380kkm 2026-09-08] ////
test('Hook 名称支持 payload 回退并在保存时保留已有元数据', async (context) => {
  for (const metadata of [undefined, { name: '元数据名称', description: '独立补充说明' }]) await context.test(metadata ? '有元数据' : '无元数据', async (context) => {
    const original = { apiVersion: 'manager.x380kkm/v1', kind: 'Plugin', id: 'plugin:hooks/start', release: { version: 'local' },
      ...(metadata ? { metadata } : {}), contributions: [{ id: 'start', point: 'hook.x380kkm/lifecycle',
        contract: { id: 'hook.x380kkm/lifecycle', range: '^1.0.0' },
        payload: { name: '处理项名称', event: 'SessionStart', handlers: [{ type: 'command', command: 'pwsh', timeout: 3 }] } }] };
    let submitted, pending;
    rendererRuntime(context, (method, params) => {
      if (method === 'document.read') return { document: structuredClone(original), baseline: structuredClone(original) };
      if (method === 'document.preview') { submitted = structuredClone(params); return { plan: { id: original.id } }; }
      return { changed: true };
    });
    const editor = createHookEditor({ run: (action) => { pending = action(); return pending; }, onSaved() {}, onDirty() {}, setStatus() {} });
    await editor.open({ name: '卡片名称', management: { documentId: `${original.id}@local` } }, 'user');
    const dialog = document.body.children[0], name = dialog.querySelectorAll('input').find((input) => input.getAttribute('aria-label') === 'Hook 名称');
    assert.equal(name.value, metadata?.name || original.contributions[0].payload.name);
    name.value = '保存名称'; await name.emit('input');
    await dialog.querySelectorAll('button').find((button) => button.textContent === '预览配置').emit('click'); await pending;
    await dialog.querySelectorAll('button').find((button) => button.textContent === '保存 Hook').emit('click'); await pending;
    assert.deepEqual(submitted.baseline, original);
    assert.deepEqual(submitted.document.metadata, { ...metadata, name: '保存名称' });
    assert.deepEqual(submitted.document.contributions[0].payload.handlers, original.contributions[0].payload.handlers);
    assert.equal(submitted.document.contributions[0].payload.name, '保存名称');
  });
});

// //// 配套说明保存成功后等待新基线再允许下一次编辑 [@x380kkm 2026-09-08] ////
test('配套说明保存后读取中断会消费旧计划并允许重新读取', async (context) => {
  const settings = { name: '配套说明', text: '保存正文', skill: 'plugin:method#main', selector: {}, enabled: true };
  const baseline = { document: { id: 'plugin:context', kind: 'Plugin', metadata: { name: settings.name } }, binding: { id: 'binding:context', kind: 'PluginBinding' } };
  let applied = false, readFails = true, pending, dirty, status;
  const calls = [];
  const get = rendererRuntime(context, (method, params) => {
    calls.push({ method, params: structuredClone(params) });
    if (method === 'context.describe') {
      if (applied && readFails) throw new Error('目录读取中断.');
      return { plugin: 'plugin:method', scope: 'project-local', name: '方法', catalog: '/private/catalog',
        skills: [{ ref: settings.skill, references: [settings.skill], name: '方法' }],
        contexts: applied ? [{ id: 'plugin:context@local', name: settings.name, settings, baseline, scope: 'project-local', editable: true }] : [] };
    }
    if (method === 'context.preview') return { name: settings.name, plan: { entries: Object.values(baseline).map((document) => ({ id: document.id, operation: 'put', before: null, after: document, changes: [] })) } };
    if (method === 'context.apply') { applied = true; return { id: 'plugin:context@local', removed: false }; }
    throw new Error(`Unexpected management call: ${method}`);
  });
  const editor = createCompanionDialog({ run: (action) => { pending = action(); return pending; }, onSaved() {},
    onDirty: (value) => { dirty = value; }, setStatus: (value) => { status = value; } });
  await editor.open('plugin:method', 'project-local');
  get('companion-text').value = settings.text; await get('companion-text').emit('input');
  await get('companion-preview').emit('click'); await pending;
  await get('companion-apply').emit('click'); await pending;
  assert.equal(dirty, false);
  assert.equal(get('companion-apply').hidden, true);
  assert.equal(get('companion-fields').disabled, true);
  assert.match(status, /已保存.*刷新目录失败/);
  await get('companion-apply').emit('click'); await pending;
  await get('companion-preview').emit('click'); await pending;
  assert.equal(calls.filter((call) => call.method === 'context.apply').length, 1);
  assert.equal(calls.filter((call) => call.method === 'context.preview').length, 1);
  readFails = false;
  await get('companion-reload').emit('click'); await pending;
  assert.equal(get('companion-fields').disabled, false);
  assert.equal(get('companion-remove').disabled, false);
  get('companion-text').value = '继续编辑'; await get('companion-text').emit('input');
  await get('companion-preview').emit('click'); await pending;
  assert.deepEqual(calls.filter((call) => call.method === 'context.preview').at(-1).params.baseline, baseline);
});

// //// 关系写入后的读取失败保持旧基线不可提交 [@x380kkm 2026-09-08] ////
test('关系保存后读取中断会阻止旧基线重试并恢复编辑', async (context) => {
  const candidate = { id: 'skill:target', kind: 'skill', name: '目标' };
  let edge = { from: 'skill:source', to: candidate.id, enabled: true, text: '原文', scope: 'user', baseline: { document: { id: 'old' }, binding: null } };
  let applied = false, readFails = true, pending, dirty, status;
  const calls = [];
  const get = rendererRuntime(context, (method, params) => {
    calls.push({ method, params: structuredClone(params) });
    if (method === 'card.relations') {
      if (applied && readFails) throw new Error('关系列表读取中断.');
      return { subject: { id: edge.from, name: '来源' }, scope: 'user', candidates: [candidate], outgoing: [structuredClone(edge)], incoming: [] };
    }
    if (method === 'card.set_relation') {
      applied = true; edge = { ...edge, text: params.text, baseline: { document: { id: 'saved' }, binding: null } };
      return { relation: edge };
    }
    throw new Error(`Unexpected management call: ${method}`);
  });
  const editor = createRelationsDialog({ run: (action) => { pending = action(); return pending; }, onSaved() {},
    onDirty: (value) => { dirty = value; }, setStatus: (value) => { status = value; } });
  await editor.open(edge.from, 'user');
  await get('relations-candidates').querySelector('button').emit('click');
  get('relation-text').value = '保存正文'; await get('relation-text').emit('input');
  await get('relation-save').emit('click'); await pending;
  assert.equal(dirty, false);
  assert.equal(get('relation-save').disabled, true);
  assert.equal(get('relations-candidates').querySelector('input').disabled, true);
  assert.match(status, /已保存.*刷新目录失败/);
  await get('relation-save').emit('click'); await pending;
  assert.equal(calls.filter((call) => call.method === 'card.set_relation').length, 1);
  readFails = false;
  await get('relations-reload').emit('click'); await pending;
  assert.equal(get('relation-save').disabled, false);
  assert.equal(get('relation-text').value, '保存正文');
  get('relation-text').value = '继续编辑'; await get('relation-text').emit('input');
  await get('relation-save').emit('click'); await pending;
  assert.equal(calls.filter((call) => call.method === 'card.set_relation').at(-1).params.baseline.document.id, 'saved');
});

// //// 源码页面显示并沿用选定配置层的读取与保存范围 [@x380kkm 2026-09-08] ////
test('用户默认与项目共享和个人源码保持各自归属', async (context) => {
  const calls = [], source = { id: 'plugin:example', kind: 'Plugin', release: { version: 'local' } };
  const get = rendererRuntime(context, (method, params) => {
    calls.push({ method, params });
    if (method === 'host.initialize') return {};
    if (method === 'card.inventory') return { items: [], edges: [], root: '/user', workspace: '/project', scannedAt: new Date().toISOString() };
    if (method === 'catalog.snapshot') return { workspace: '/project', scope: params.scope, catalog: `/${params.scope}/catalog`,
      documents: [{ id: 'plugin:example@local', scope: params.scope, kind: 'Plugin' }], graph: { nodes: [], edges: [] }, diagnostics: [] };
    if (method === 'document.read') return { document: source, baseline: source };
    if (method === 'document.preview') return { plan: { id: 'plugin:example@local', before: source, after: source, scope: params.scope, changes: [] } };
    if (method === 'document.apply') return { id: params.plan.id, document: source };
    throw new Error(`Unexpected management call: ${method}`);
  });
  window.manager.getContext = async () => ({ ok: true, result: { workspace: '/project' } });
  const { start } = await import('../desktop/renderer/ui.mjs'); start(); await new Promise(setImmediate);
  await get('inventory-kinds').children.find((node) => node.textContent === '配置源码').emit('click');
  for (const [scope, label] of [['user', '用户级默认'], ['project', '项目共享设置'], ['project-local', '项目个人设置']]) {
    await get(`${scope}-catalog-button`).emit('click');
    assert.equal(get(`${scope}-catalog-button`).getAttribute('aria-pressed'), 'true');
    if (scope !== 'user') assert.equal(get('catalog-project-scopes').open, true);
    assert.ok(get('catalog-caption').textContent.startsWith(label));
    await get('document-list').children[0].emit('click');
    const properties = get('document-properties').children;
    assert.equal(properties[properties.findIndex((item) => item.textContent === '配置归属') + 1].textContent, label);
    await get('preview-button').emit('click'); await get('apply-button').emit('click');
    assert.equal(calls.filter((call) => call.method === 'document.preview').at(-1).params.scope, scope);
    assert.equal(calls.filter((call) => call.method === 'document.apply').at(-1).params.plan.scope, scope);
  }
});

// //// 使用设置在原配置层预览恢复继承 [@x380kkm 2026-09-08] ////
test('项目共享和个人使用设置均可在本层恢复继承', async (context) => {
  for (const [scope, label] of [['project', '项目共享设置'], ['project-local', '项目个人设置']]) await context.test(scope, async (context) => {
    const binding = { id: `binding:${scope}/plugin:method`, enabled: true, plugin: { id: 'plugin:method' }, target: { selector: {} } };
    let removal, pending, previewScope;
    const get = rendererRuntime(context, (method, params) => {
      if (method === 'usage.describe') return { plugin: 'plugin:method', scope, name: '方法', release: { version: 'local' },
        catalog: `/${scope}/catalog`, bindings: [binding], preferred: binding.id, members: [], effective: [], defaults: {} };
      if (method === 'document.preview_remove') { removal = params; return { plan: { id: binding.id, scope } }; }
      throw new Error(`Unexpected management call: ${method}`);
    });
    const editor = createUsageDialog({ run: (action) => { pending = action(); return pending; }, onDirty() {}, setStatus() {},
      onPlan: (_result, scope) => { previewScope = scope; } });
    await editor.open('plugin:method', scope);
    assert.ok(get('usage-location').textContent.startsWith(label));
    assert.equal(get('usage-inherit').hidden, false);
    await get('usage-inherit').emit('click'); await pending;
    assert.deepEqual(removal, { id: binding.id, baseline: binding, scope });
    assert.equal(previewScope, scope);
  });
});

// //// 项目宿主检查只读取状态并引导用户打开预览 [@x380kkm 2026-09-08] ////
test('重新打开接管项目会只读显示待应用或阻断状态', async (context) => {
  for (const status of ['pending', 'blocked']) await context.test(status, async (context) => {
    const calls = [];
    const get = rendererRuntime(context, (method, params) => {
      calls.push({ method, params });
      if (method === 'card.inventory') return { items: [], edges: [], root: '/user', workspace: '/project', scannedAt: new Date().toISOString(), hostControl: { enabled: true } };
      if (method === 'host.inspect') return { scope: 'project-local', status, files: [], ownershipChanged: status === 'pending',
        diagnostics: status === 'blocked' ? [{ message: '来源发生冲突.' }] : [] };
      throw new Error(`Unexpected management call: ${method}`);
    });
    let settingsScope;
    const { createInventoryView } = await import('../desktop/renderer/inventory-view.mjs');
    const view = createInventoryView({ run: (action) => action(), onDirty() {}, setStatus() {}, onProject: async () => '/project', onSettings: (scope) => { settingsScope = scope; } });
    await view.openProject();
    const hint = get('inventory-scope-hint');
    assert.equal(hint.hidden, false);
    assert.match(hint.children[0].textContent, status === 'pending' ? /待应用/ : /应用受阻.*来源发生冲突/);
    await hint.querySelector('button').emit('click');
    assert.equal(settingsScope, 'project-local');
    assert.deepEqual(calls.map((call) => call.method), ['card.inventory', 'host.inspect']);
    assert.equal(calls[1].params.scope, 'project-local');
    await view.refresh(true);
    assert.equal(hint.hidden, false);
    assert.deepEqual(calls.map((call) => call.method), ['card.inventory', 'host.inspect', 'card.inventory', 'host.inspect']);
  });
});

// //// 离开项目页面后忽略迟到的宿主检查结果 [@x380kkm 2026-09-08] ////
test('迟到的项目检查保持用户页面状态', async (context) => {
  let resolveInspection;
  const get = rendererRuntime(context, (method, params) => {
    if (method === 'card.inventory') return { items: [], edges: [], root: params.scope, workspace: '/project', scannedAt: new Date().toISOString(), hostControl: { enabled: true } };
    if (method === 'host.inspect') return new Promise((resolve) => { resolveInspection = resolve; });
    throw new Error(`Unexpected management call: ${method}`);
  });
  const { createInventoryView } = await import('../desktop/renderer/inventory-view.mjs');
  const view = createInventoryView({ run: (action) => action(), onDirty() {}, setStatus() {}, onProject: async () => '/project' });
  const opened = view.openProject(); await new Promise(setImmediate);
  await view.openUser();
  resolveInspection({ status: 'pending', files: [], diagnostics: [] }); await opened;
  assert.equal(get('inventory-root').textContent, 'user');
  assert.equal(get('inventory-scope-hint').hidden, true);
});
