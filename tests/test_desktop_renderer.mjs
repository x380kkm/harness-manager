// audience: internal
// # desktop-renderer-state
// 界面状态使用内存 DOM 和可控管理响应验证. 真实传输由桌面 RPC 集成测试覆盖.

import assert from 'node:assert/strict';
import test from 'node:test';
import { planDifferences } from '../desktop/renderer/document-diff.mjs';
import { Element, rendererRuntime } from './renderer-runtime.mjs';


// //// 分离字段变化保留中间未变内容 [@x380kkm 2026-09-06] ////
test('字段差异仅标记核心计划指定的非相邻字段', () => {
  const before = { metadata: { name: '原名称', description: '保持说明' }, target: { selector: '原范围' } };
  const after = { metadata: { name: '新名称', description: '保持说明' }, target: { selector: '新范围' } };
  const rows = planDifferences({ before, after, changes: [
    { path: '/metadata/name', operation: 'replace', before: '原名称', after: '新名称' },
    { path: '/target/selector', operation: 'replace', before: '原范围', after: '新范围' },
  ] });
  for (const side of [rows.before, rows.after]) {
    assert.deepEqual(side.filter((row) => row.changed).map((row) => row.path), ['/metadata/name', '/target/selector']);
    assert.equal(side.find((row) => row.path === '/metadata/description').changed, false);
  }
});

// //// 归属确认与中断备份使用独立的可执行状态 [@x380kkm 2026-09-08] ////
test('文件未变时可以确认归属, 混合中断备份显示阻断原因', async (context) => {
  const previous = { document: globalThis.document, window: globalThis.window };
  context.after(() => Object.assign(globalThis, previous));
  globalThis.document = { body: new Element('body'), createElement: (tag) => new Element(tag), getElementById: () => null };
  globalThis.window = { manager: { call: async (method) => {
    if (method === 'host.status') return { ok: true, result: {
      enabled: true, initialized: true, targetRoot: '/host', backupRoot: '/backup', baseline: {},
      backups: [{ id: 'interrupted', label: '中断时的配置', createdAt: '2026-09-08', files: ['config.toml'],
        restorable: false, restoreError: '文件属于混合写入状态, 请确认内容归属.' }],
    } };
    if (method === 'host.preview') return { ok: true, result: { planId: 'owned', files: [], ownershipChanged: true } };
    throw new Error(`Unexpected method ${method}`);
  } } };
  let pending;
  const { createHostSettings } = await import('../desktop/renderer/host-settings.mjs');
  const settings = createHostSettings({ run: (action) => { pending = action(); return pending; },
    onSaved: async () => {}, onSource() {}, setStatus() {} });
  await settings.open();
  const dialog = document.body.children[0];
  const restore = dialog.querySelectorAll('button').find((button) => button.textContent === '预览恢复');
  assert.equal(restore.disabled, true);
  assert.ok(dialog.querySelectorAll('p').some((paragraph) => paragraph.textContent.includes('混合写入状态')));
  await dialog.querySelectorAll('button').find((button) => button.textContent === '预览待应用设置').emit('click');
  await pending;
  assert.equal(dialog.querySelectorAll('button').find((button) => button.textContent === '应用到文件').hidden, false);
  assert.ok(dialog.querySelectorAll('p').some((paragraph) => paragraph.textContent.includes('更新管理归属')));
});

// //// 接管开关在弹窗内显示实际应用结果与冲突原因 [@x380kkm 2026-09-08] ////
test('开启接管受阻时保留来源诊断, 关闭后显示实际结果', async (context) => {
  const previous = { document: globalThis.document, window: globalThis.window };
  context.after(() => Object.assign(globalThis, previous));
  globalThis.document = { body: new Element('body'), createElement: (tag) => new Element(tag), getElementById: () => null };
  let enabled = false, status, pending;
  globalThis.window = { manager: { call: async (method, params) => {
    if (method === 'host.status') return { ok: true, result: {
      enabled, initialized: true, targetRoot: '/host', backupRoot: '/backup', baseline: {}, backups: [],
    } };
    if (method === 'host.set_enabled') {
      enabled = params.enabled;
      return { ok: true, result: { enabled, initialized: true, targetRoot: '/host', backupRoot: '/backup', baseline: {}, backups: [], hostSync: enabled
        ? { status: 'blocked', message: '接管已开启, 宿主应用受阻.', diagnostics: [{ message: '规则来源片段无法唯一匹配.' }] }
        : { status: 'disabled', message: '接管已关闭, 宿主继续使用当前配置.' } } };
    }
    throw new Error(`Unexpected method ${method}`);
  } } };
  const { createHostSettings } = await import('../desktop/renderer/host-settings.mjs');
  const settings = createHostSettings({ run: (action) => { pending = action(); return pending; },
    onSaved: async () => {}, onSource() {}, setStatus(message) { status = message; } });
  await settings.open();
  const dialog = document.body.children[0];
  const toggle = dialog.querySelector('[aria-label="开启配置接管"]');
  toggle.checked = true;
  await toggle.emit('change'); await pending;
  assert.equal(dialog.querySelector('.settings-feedback').textContent, '接管已开启, 宿主应用受阻.');
  assert.equal(status, '接管已开启, 宿主应用受阻.');
  assert.ok(dialog.querySelectorAll('.diagnostic').some((note) => note.textContent === '规则来源片段无法唯一匹配.'));
  const updatedToggle = dialog.querySelector('[aria-label="开启配置接管"]');
  assert.equal(updatedToggle.checked, true);
  updatedToggle.checked = false;
  await updatedToggle.emit('change'); await pending;
  assert.equal(dialog.querySelector('.settings-feedback').textContent, '接管已关闭, 宿主继续使用当前配置.');
  assert.equal(dialog.querySelectorAll('.diagnostic').length, 0);
});

// //// 指针转义与数组字段保持核心差异范围 [@x380kkm 2026-09-06] ////
test('字段差异保留转义键和整体数组的实际范围', () => {
  const rows = planDifferences({
    before: { 'a/b~c': '旧值', entries: ['保留项', '旧项'], untouched: 3 },
    after: { 'a/b~c': '新值', entries: ['保留项', '新项'], untouched: 3 },
    changes: [{ path: '/a~1b~0c', operation: 'replace' }, { path: '/entries', operation: 'replace' }],
  });
  assert.equal(rows.after.find((row) => row.path === '/a~1b~0c').changed, true);
  assert.equal(rows.after.find((row) => row.path === '/entries/0').changed, true);
  assert.equal(rows.after.find((row) => row.path === '/untouched').changed, false);
});

// //// 节点切换和冲突比较保持编辑与基线的独立性 [@x380kkm 2026-09-06] ////
test('同一声明节点切换保留编辑, 合并确认后才采用最新基线', async (context) => {
  const elements = new Map();
  const get = (id) => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const originalGlobals = { document: globalThis.document, window: globalThis.window, localStorage: globalThis.localStorage };
  context.after(() => {
    for (const [name, value] of Object.entries(originalGlobals)) {
      if (value === undefined) delete globalThis[name]; else globalThis[name] = value;
    }
  });
  globalThis.document = { getElementById: get, createElement: () => new Element(), createElementNS: () => new Element(), body: new Element(), documentElement: new Element() };
  globalThis.localStorage = { getItem: () => null, setItem() {} };
  const id = 'plugin:tools/index@1.0.0';
  const original = { id: 'plugin:tools/index', kind: 'Plugin', release: { version: '1.0.0' }, metadata: { name: '项目索引', description: '保留来源' } };
  let persisted = structuredClone(original);
  let confirmations = 0;
  let exitProtected = false;
  const calls = [];
  const graph = {
    nodes: [{ id, documentId: id, label: '项目索引', kind: 'Plugin' }, ...['symbols', 'relations'].map((name) => ({ id: `${id}#${name}`, documentId: id, label: name, kind: 'skill' }))],
    edges: ['symbols', 'relations'].map((name) => ({ id: name, from: id, to: `${id}#${name}`, label: 'contains' })),
  };
  globalThis.window = {
    confirm: () => { confirmations += 1; return false; },
    manager: {
      getContext: async () => ({ ok: true, result: { workspace: null } }),
      setDirty(value) { exitProtected = value; },
      call: async (method, params) => {
        calls.push({ method, params: structuredClone(params) });
        if (method === 'host.initialize') return { ok: true, result: { initialized: true, enabled: true } };
        if (method === 'card.inventory') return { ok: true, result: { root: 'profile/.codex', scannedAt: new Date().toISOString(), items: [], edges: [], diagnostics: [] } };
        if (method === 'catalog.snapshot') return { ok: true, result: { workspace: null, scope: params.scope, catalog: 'profile/.harness/catalog.json', documents: [{ id, kind: 'Plugin', name: '项目索引', version: '1.0.0', scope: params.scope }], graph, diagnostics: [] } };
        if (method === 'document.read') return persisted === null
          ? { ok: false, error: { code: 'not_found', message: '该身份当前没有登记.' } }
          : { ok: true, result: { document: structuredClone(persisted), baseline: structuredClone(persisted) } };
        if (method === 'document.preview') {
          if (JSON.stringify(params.baseline) !== JSON.stringify(persisted)) return { ok: false, error: { code: 'catalog-conflict', message: '登记已变化.', details: { documentId: id } } };
          const changes = params.baseline === null
            ? Object.entries(params.document).map(([key, value]) => ({ path: `/${key}`, operation: 'add', after: value }))
            : [{ path: '/metadata/name', operation: 'replace' }];
          return { ok: true, result: { plan: { operation: 'put', id, before: params.baseline, after: params.document, changes }, diagnostics: [] } };
        }
        if (method === 'document.preview_remove') {
          if (JSON.stringify(params.baseline) !== JSON.stringify(persisted)) return { ok: false, error: { code: 'catalog-conflict', message: '登记已变化.', details: { documentId: id } } };
          const changes = Object.entries(params.baseline).map(([key, value]) => ({ path: `/${key}`, operation: 'remove', before: value }));
          return { ok: true, result: { plan: { operation: 'remove', id, before: params.baseline, after: null, changes }, diagnostics: [] } };
        }
        throw new Error(`Unexpected management call: ${method}`);
      },
    },
  };
  const { start } = await import('../desktop/renderer/ui.mjs');
  start();
  await new Promise(setImmediate);
  assert.equal(get('inventory-workspace').hidden, false);
  assert.equal(get('catalog-workspace').hidden, true);
  await get('inventory-kinds').children.find((element) => element.textContent === '配置源码').emit('click');
  assert.equal(get('workspace-label').textContent, '用户级默认');
  assert.equal(get('import-button').disabled, false);
  assert.equal(get('project-catalog-button').disabled, true);
  await get('document-list').children[0].emit('click');
  assert.equal(exitProtected, false);
  await get('recover-button').emit('click');
  get('merge-result').value = '尚在编辑的合并结果';
  await get('merge-result').emit('input');
  assert.equal(exitProtected, true);
  await get('merge-close').emit('click');
  assert.equal(exitProtected, false);
  const readsBeforeSwitch = calls.filter((call) => call.method === 'document.read').length;
  const local = { ...original, metadata: { ...original.metadata, name: '本地名称' } };
  get('json-editor').value = JSON.stringify(local, null, 2);
  await get('json-editor').emit('input');
  await get('graph-button').emit('click');
  const canvas = get('graph-canvas');
  for (const name of ['symbols', 'relations']) await canvas.querySelectorAll().find((node) => node.getAttribute('data-node-id') === `${id}#${name}`).emit('click');
  assert.equal(get('json-editor').value, JSON.stringify(local, null, 2));
  assert.equal(confirmations, 0);
  assert.equal(calls.filter((call) => call.method === 'document.read').length, readsBeforeSwitch);
  await get('graph-in').emit('click');
  const viewport = canvas.getAttribute('viewBox');
  await get('list-button').emit('click');
  await get('graph-button').emit('click');
  await get('refresh-button').emit('click');
  assert.equal(canvas.getAttribute('viewBox'), viewport);
  assert.equal(calls.filter((call) => call.method === 'catalog.snapshot').length, 2);

  persisted = { ...original, metadata: { ...original.metadata, description: '磁盘的新摘要' } };
  await get('remove-button').emit('click');
  assert.equal(get('merge-editing').hidden, true);
  await get('merge-confirm').emit('click');
  assert.equal(calls.at(-1).method, 'document.preview_remove');
  assert.deepEqual(calls.at(-1).params.baseline, persisted);
  assert.equal(JSON.parse(get('plan-content').textContent).operation, 'remove');
  assert.equal(get('json-editor').value, JSON.stringify(local, null, 2));
  await get('preview-close').emit('click');
  await get('preview-button').emit('click');
  assert.deepEqual(calls.filter((call) => call.method === 'document.preview').at(-1).params.baseline, original);
  assert.equal(get('merge-dialog').open, true);
  assert.equal(get('merge-result').value, '');
  assert.equal(get('json-editor').value, JSON.stringify(local, null, 2));
  await get('merge-close').emit('click');
  await get('preview-button').emit('click');
  assert.deepEqual(calls.filter((call) => call.method === 'document.preview').at(-1).params.baseline, original);
  await get('merge-use-latest').emit('click');
  const merged = JSON.parse(get('merge-result').value);
  merged.metadata.name = '本地名称';
  get('merge-result').value = JSON.stringify(merged);
  await get('merge-confirm').emit('click');
  const confirmed = calls.filter((call) => call.method === 'document.preview').at(-1).params;
  assert.deepEqual(confirmed.baseline, persisted);
  assert.equal(confirmed.scope, 'user');
  assert.equal(confirmed.document.metadata.name, '本地名称');
  assert.equal(confirmed.document.metadata.description, '磁盘的新摘要');
  assert.equal(get('preview-dialog').open, true);
  await get('preview-close').emit('click');
  await get('preview-button').emit('click');
  assert.deepEqual(calls.filter((call) => call.method === 'document.preview').at(-1).params.baseline, persisted);

  await get('preview-close').emit('click');
  persisted = null;
  const retainedText = get('json-editor').value;
  await get('recover-button').emit('click');
  assert.equal(get('merge-use-latest').disabled, true);
  assert.equal(get('json-editor').value, retainedText);
  assert.equal(get('merge-result').value, '');
  await get('merge-use-local').emit('click');
  await get('merge-confirm').emit('click');
  assert.equal(calls.filter((call) => call.method === 'document.preview').at(-1).params.baseline, null);
  assert.equal(get('preview-dialog').open, true);
  assert.equal(calls.some((call) => call.method === 'document.apply'), false);
});

// //// 为延迟请求保留完整浏览入口和来源卡片 [@x380kkm 2026-09-07] ////
function inventoryFixture(root = 'user') {
  const items = [{ id: 'rule', kind: 'rule', name: 'PowerShell 使用规范', summary: '转义与编码', content: '使用 UTF8 编码.', path: '/rules', details: { catalogScope: 'user' } },
    { id: 'official', kind: 'skill', name: 'Official tool', summary: '来源', path: '/official', details: {} }];
  return { root, scannedAt: new Date().toISOString(), items, edges: [], diagnostics: [],
    groups: items.map((item) => ({ id: `g:${item.id}`, primaryId: item.id, itemIds: [item.id], category: item.kind, name: item.name, origin: item.id === 'official' ? 'official' : 'user', counts: { [item.kind]: 1 } })),
    cardManagement: { rule: { supported: true, scope: 'user', userState: 'enabled', effectiveEnabled: true } } };
}


// //// 保持导航节点与个人卡片稳定并采用最新范围响应 [@x380kkm 2026-09-07] ////
test('筛选复用导航, 官方展开保留个人卡片, 过期盘点保持隔离', async (context) => {
  const requests = [];
  const get = rendererRuntime(context, (method, params) => {
    assert.equal(method, 'card.inventory');
    return new Promise((resolve) => requests.push({ params, resolve }));
  });
  const { createInventoryView } = await import('../desktop/renderer/inventory-view.mjs');
  const view = createInventoryView({ run: (action) => action(), setStatus() {}, onDirty() {}, onProject: async () => '/project' });
  const navigation = [...get('inventory-kinds').children];
  const initial = view.refresh();
  assert.ok(navigation.length > 0);
  assert.equal(get('inventory-empty').textContent, '正在读取本机内容...');
  requests[0].resolve(inventoryFixture()); await initial;
  const cards = get('inventory-list').children[0];
  get('inventory-official').open = true; await get('inventory-official').emit('toggle');
  assert.equal(get('inventory-list').children[0], cards);
  get('inventory-search').value = 'powershell'; await get('inventory-search').emit('input');
  assert.deepEqual(get('inventory-kinds').children, navigation);
  assert.equal(get('inventory-list').children[0].children.length, 1);
  const previous = view.refresh(), latest = view.refresh(true);
  assert.equal(requests[2].params.refresh, true);
  requests[2].resolve(inventoryFixture('latest')); await latest;
  requests[1].resolve(inventoryFixture('old')); await previous;
  assert.equal(get('inventory-root').textContent, 'latest');
  assert.deepEqual(get('inventory-kinds').children, navigation);
  const userRead = view.refresh(), projectRead = view.openProject();
  await new Promise(setImmediate);
  assert.equal(requests[4].params.scope, 'project-local');
  requests[4].resolve({ ...inventoryFixture('project'), workspace: '/project' }); await projectRead;
  requests[3].resolve(inventoryFixture('user')); await userRead;
  assert.equal(get('inventory-root').textContent, 'project');
  assert.equal(get('inventory-scope-label').hidden, false);
});

// //// 在读取和写入等待期间保持浏览并拒绝重复配置动作 [@x380kkm 2026-09-07] ////
test('后台等待保留浏览, 配置动作防重入, 较慢声明响应保持隔离', async (context) => {
  let resolveInitial, resolveWrite;
  const documentReads = [], calls = [];
  let inventories = 0;
  const documents = ['one', 'two'].map((id) => ({ id, kind: 'Plugin', name: id, version: '1', scope: 'user' }));
  const get = rendererRuntime(context, (method, params) => {
    calls.push({ method, params });
    if (method === 'host.initialize') return { initialized: true };
    if (method === 'card.inventory') return ++inventories === 1 ? new Promise((resolve) => { resolveInitial = resolve; }) : inventoryFixture();
    if (method === 'card.configure') return new Promise((resolve) => { resolveWrite = resolve; });
    if (method === 'catalog.snapshot') return { workspace: null, scope: 'user', catalog: '/catalog', documents, graph: { nodes: [], edges: [] }, diagnostics: [] };
    if (method === 'document.read') return new Promise((resolve) => documentReads.push({ id: params.id, resolve }));
    throw new Error(`Unexpected management call: ${method}`);
  });
  const { start } = await import('../desktop/renderer/ui.mjs');
  start(); await new Promise(setImmediate);
  assert.notEqual(get('shell').inert, true);
  assert.equal(get('project-page-button').disabled, true);
  const navigation = [...get('inventory-kinds').children];
  get('inventory-search').value = 'PowerShell'; await get('inventory-search').emit('input');
  assert.deepEqual(get('inventory-kinds').children, navigation);
  resolveInitial(inventoryFixture()); await new Promise(setImmediate);
  const activation = get('inventory-list').querySelector('select');
  activation.value = 'disabled'; await activation.emit('change');
  assert.equal(activation.disabled, true);
  activation.value = 'enabled'; await activation.emit('change');
  assert.equal(calls.filter((call) => call.method === 'card.configure').length, 1);
  assert.match(get('status-message').textContent, /当前动作未执行/);
  await get('inventory-list').querySelector('[data-select-id]').emit('click');
  assert.equal(get('inventory-inspector').hidden, false);
  assert.notEqual(get('shell').inert, true);
  resolveWrite({}); await new Promise(setImmediate);
  await get('inventory-kinds').children.find((node) => node.textContent === '配置源码').emit('click');
  const first = get('document-list').children[0].emit('click');
  const second = get('document-list').children[1].emit('click');
  assert.equal(documentReads.length, 2);
  assert.equal(get('json-editor').disabled, true);
  assert.equal(get('preview-button').disabled, true);
  const document = { id: 'two', kind: 'Plugin', metadata: { name: 'second' } };
  documentReads[1].resolve({ document, baseline: document }); await second;
  documentReads[0].resolve({ document: { id: 'one' }, baseline: { id: 'one' } }); await first;
  assert.equal(JSON.parse(get('json-editor').value).id, 'two');
  assert.equal(get('json-editor').disabled, false);
  assert.equal(get('project-catalog-button').disabled, true);
});

// //// 模块快捷启停保持条件绑定独立 [@x380kkm 2026-09-08] ////
test('模块快捷启停保留独立接收条件的自定义绑定', async (context) => {
  for (const scope of ['user', 'project-local']) await context.test(scope, async (context) => {
    const custom = { id: 'binding:reviewer-only', plugin: { id: 'plugin:module/commands' }, enabled: false, target: { selector: { agent: 'reviewer' } } };
    const defaultId = `binding:${scope}/${custom.plugin.id}`;
    let bindings = [structuredClone(custom)], pending;
    const calls = [];
    const item = { id: 'module:commands', name: '命令模块', kind: 'module', path: '/catalog', details: {
      pluginId: custom.plugin.id, documentId: `${custom.plugin.id}@local`, members: [], memberCounts: {},
    } };
    const get = rendererRuntime(context, (method, params) => {
      calls.push({ method, params: structuredClone(params) });
      if (method === 'card.inventory') return { root: scope, scannedAt: new Date().toISOString(), edges: [],
        items: [{ ...item, details: { ...item.details, configuredState: bindings.find((binding) => binding.id === defaultId)?.enabled === true ? 'enabled' : 'inherit' } }],
        groups: [{ id: 'g:module', primaryId: item.id, itemIds: [item.id], category: 'module', name: item.name, origin: 'user', counts: { module: 1 } }],
      };
      if (method === 'usage.describe') return { bindings: structuredClone(bindings), contextBindings: bindings.filter((binding) => binding.id === defaultId),
        plugin: item.details.documentId, release: { version: 'local' }, selectedPlugin: bindings.some((binding) => binding.id === defaultId) ? item.details.documentId : null,
        suggestedId: defaultId, preferred: bindings.find((binding) => binding.id === defaultId)?.id || custom.id };
      if (method === 'usage.preview') return { plan: { after: { ...(params.baseline || { id: defaultId }), enabled: params.settings.state === 'enabled' } } };
      if (method === 'document.preview_remove') return { plan: { id: params.id, after: null } };
      if (method === 'document.apply') {
        const id = params.plan.after?.id || params.plan.id;
        bindings = bindings.filter((binding) => binding.id !== id);
        if (params.plan.after) bindings.push(params.plan.after);
        return {};
      }
      throw new Error(`Unexpected management call: ${method}`);
    });
    const { createInventoryView } = await import('../desktop/renderer/inventory-view.mjs');
    const view = createInventoryView({ run: (action) => { pending = action(); return pending; }, setStatus() {}, onDirty() {}, onProject: async () => '/project' });
    if (scope === 'user') await view.refresh(); else await view.openProject();
    for (const value of ['inherit', 'enabled', 'disabled', 'inherit']) {
      const control = get('inventory-list').querySelector('select');
      control.value = value; await control.emit('change'); await pending;
      assert.deepEqual(bindings.find((binding) => binding.id === custom.id), custom);
    }
    assert.deepEqual(bindings, [custom]);
    const previews = calls.filter((call) => call.method === 'usage.preview');
    assert.equal(previews[0].params.baseline, null);
    assert.equal(previews[1].params.baseline.id, defaultId);
    assert.ok(previews.every((call) => call.params.scope === scope));
    assert.deepEqual(calls.filter((call) => call.method === 'document.preview_remove').map((call) => call.params.id), [defaultId]);
  });
});

// //// 唯一适用的自定义模块绑定沿用原始设置与身份 [@x380kkm 2026-09-10] ////
test('模块快捷启停和继承使用当前自定义绑定', async (context) => {
  const original = { id: 'binding:custom/module', plugin: { id: 'plugin:module/custom' }, enabled: true,
    target: { selector: { user: 'current', host: 'codex' } }, options: { mode: 'personal' } };
  let bindings = [structuredClone(original)], pending;
  const calls = [];
  const item = { id: 'module:custom', name: '自定义模块', kind: 'module', path: '/catalog', details: {
    pluginId: original.plugin.id, documentId: `${original.plugin.id}@local`, members: [], memberCounts: {} } };
  const get = rendererRuntime(context, (method, params) => {
    calls.push({ method, params: structuredClone(params) });
    if (method === 'card.inventory') return { root: '/user', scannedAt: new Date().toISOString(), edges: [],
      items: [{ ...item, details: { ...item.details, configuredState: bindings[0]?.enabled ? 'enabled' : 'disabled' } }],
      groups: [{ id: 'g:custom', primaryId: item.id, itemIds: [item.id], category: 'module', name: item.name, origin: 'user', counts: { module: 1 } }] };
    if (method === 'usage.describe') return { bindings: structuredClone(bindings), contextBindings: structuredClone(bindings), suggestedId: 'binding:user/module',
      plugin: item.details.documentId, release: { version: 'local' }, selectedPlugin: bindings.length ? item.details.documentId : null };
    if (method === 'usage.preview') return { plan: { after: { ...params.baseline, enabled: params.settings.state === 'enabled' } } };
    if (method === 'document.preview_remove') return { plan: { id: params.id, after: null } };
    if (method === 'document.apply') { bindings = params.plan.after ? [params.plan.after] : []; return {}; }
    throw new Error(method);
  });
  const { createInventoryView } = await import('../desktop/renderer/inventory-view.mjs');
  const view = createInventoryView({ run: (action) => { pending = action(); return pending; }, setStatus() {}, onDirty() {} });
  await view.refresh();
  get('inventory-show-disabled').checked = true;
  await get('inventory-show-disabled').emit('change');
  for (const state of ['disabled', 'enabled', 'inherit']) {
    const control = get('inventory-list').querySelector('select');
    control.value = state; await control.emit('change'); await pending;
    if (state !== 'inherit') {
      assert.equal(bindings[0].id, original.id);
      assert.deepEqual(bindings[0].options, original.options);
      assert.deepEqual(bindings[0].target, original.target);
    }
  }
  assert.equal(bindings.length, 0);
  assert.deepEqual(calls.filter((call) => call.method === 'document.preview_remove').map((call) => call.params.id), [original.id]);
});

// //// 显示编辑影响并在保存后保留独立的刷新结果 [@x380kkm 2026-09-08] ////
test('Hook 和模块显示预览诊断, 保存后单独报告刷新失败', async (context) => {
  const { createHookEditor } = await import('../desktop/renderer/hook-editor.mjs');
  const { createModuleEditor } = await import('../desktop/renderer/module-editor.mjs');
  for (const [name, create, previewLabel, saveLabel, method] of [
    ['Hook', createHookEditor, '预览配置', '保存 Hook', 'document.apply'],
    ['模块', createModuleEditor, '预览组合', '保存模块', 'module.apply'],
  ]) await context.test(name, async (context) => {
    const calls = [];
    rendererRuntime(context, (method, params) => {
      calls.push({ method, params });
      if (method === 'module.describe') return { editable: true, settings: { name: '命令模块', description: '', members: [{ itemId: 'rule', role: '' }] },
        candidates: [{ itemId: 'rule', name: '命令规范', kind: 'rule', supported: true }] };
      if (method.endsWith('.preview')) return { plan: { id: 'saved' },
        diagnostics: [{ severity: 'error', code: 'catalog_identity_conflict', scope: 'project', message: '共享发布包含不同正文.' }] };
      return { changed: true };
    });
    let pending, dirty, status;
    const editor = create({ run: (action) => { pending = action(); return pending; }, onDirty: (value) => { dirty = value; },
      onSaved: () => { throw new Error('目录读取中断.'); }, setStatus: (value) => { status = value; } });
    await editor.open(null, 'user');
    const dialog = document.body.children[0];
    if (name === 'Hook') {
      const field = (label) => dialog.querySelectorAll('input, select, textarea').find((input) => input.getAttribute('aria-label') === label);
      field('Hook 名称').value = '命令检查';
      field('Hook 事件').value = 'SessionStart';
      field('Hook 处理项').value = JSON.stringify([{ type: 'command', command: 'pwsh' }]);
      await field('Hook 名称').emit('input');
    }
    await dialog.querySelectorAll('button').find((button) => button.textContent === previewLabel).emit('click'); await pending;
    assert.match(dialog.querySelector('.diagnostic').textContent, /项目共享设置: 共享发布包含不同正文/);
    await dialog.querySelectorAll('button').find((button) => button.textContent === saveLabel).emit('click'); await pending;
    assert.equal(calls.filter((call) => call.method === method).length, 1);
    assert.equal(dialog.open, false);
    assert.equal(dirty, false);
    assert.match(status, /已保存.*刷新目录失败: 目录读取中断/);
  });
});
