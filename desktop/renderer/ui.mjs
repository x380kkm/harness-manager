// audience: internal
// # manager-ui
// 页面以磁盘目录为读取来源. 当前编辑和关系图共享声明选择.

import { filterDocuments, renderDocuments, renderKinds } from './catalog-view.mjs';
import { createEditor } from './editor.mjs';
import { createUsageDialog } from './usage-dialog.mjs';
import { createContentPreview } from './content-preview.mjs';
import { createCompanionDialog } from './companion-dialog.mjs';
import { createInventoryView } from './inventory-view.mjs';
import { createHostSettings } from './host-settings.mjs';
import { createModuleEditor } from './module-editor.mjs';
import { createHookEditor } from './hook-editor.mjs';
import { createGraph, graphForDocuments, registeredDocumentId } from './graph.mjs';
import { catalogScopeNames, element, renderDiagnostics, request, unwrap } from './view-utils.mjs';

// //// 显示管理动作的状态文本 [@x380kkm 2026-09-06] ////
function setStatus(message) {
  element('status-message').textContent = message;
}

// //// 显示管理错误和具体证据 [@x380kkm 2026-09-06] ////
function showError(error) {
  element('error-message').textContent = error.message || String(error);
  element('error-content').textContent = JSON.stringify({ code: error.code, details: error.details }, null, 2);
  element('error-details').hidden = !error.code && !error.details;
  element('error-panel').hidden = false;
  setStatus('请查看操作反馈中的具体原因.');
}

// //// 初始化本地管理页面 [@x380kkm 2026-09-06] ////
export function start() {
  const state = { view: 'inventory', pageScope: 'user', workspace: null, catalog: null, scope: 'user', documents: [], graph: { nodes: [], edges: [] }, diagnostics: [], kind: '', query: '', mode: 'list', selected: null, selectedNode: null, busy: false, readers: 0 };
  const graph = createGraph(element('graph-canvas'), (id) => run(() => selectGraphNode(id), { readOnly: true }));
  const busyControls = new Set();
  let catalogVersion = 0, documentVersion = 0, editorRevision = 0;
  let editorDirty = false;
  let usageDirty = false;
  let companionDirty = false;
  let relationsDirty = false;
  const editor = createEditor({ run, setStatus, onDirty: (dirty) => { editorDirty = dirty; editorRevision += 1; updateExitProtection(); }, onApplied, onRecovered });
  const usage = createUsageDialog({ run, setStatus, onPlan: stageUsagePlan, onDraft: stageUsageDraft,
    onDirty: (dirty) => { usageDirty = dirty; updateExitProtection(); } });
  const contentPreview = createContentPreview({ run: (action) => run(action, { readOnly: true }), setStatus, onUsage: (plugin, scope) => run(() => openUsage(plugin, scope)) });
  const inventory = createInventoryView({ run, setStatus, onProject: chooseInventoryProject,
    onSettings: (scope) => run(() => settings.open(scope)), onSource: (scope) => run(() => openSource(scope)),
    onModule: (item, scope) => run(() => modules.open(item, scope)),
    onHook: (item, scope) => run(() => hooks.open(item, scope)),
    onScopeChanged: (scope) => { state.pageScope = scope; showView('inventory'); },
    onDirty: (value) => { relationsDirty = value; updateExitProtection(); } });
  const companions = createCompanionDialog({ run, setStatus, onSaved: refresh,
    onDirty: (dirty) => { companionDirty = dirty; updateExitProtection(); } });
  const settings = createHostSettings({ run, setStatus, onSaved: () => inventory.refresh(), onSource: (scope) => run(() => openSource(scope)) });
  const modules = createModuleEditor({ run, setStatus, onSaved: () => inventory.refresh(), onDirty: (value) => { relationsDirty = value; updateExitProtection(); } });
  const hooks = createHookEditor({ run, setStatus, onSaved: () => inventory.refresh(), onDirty: (value) => { relationsDirty = value; updateExitProtection(); } });

  // //// 从系统入口查看当前范围的配置源码 [@x380kkm 2026-09-07] ////
  async function openSource(scope) {
    if (window.manager.readOnly) { setStatus('配置源码在桌面应用中查看与编辑.'); return; }
    if (!canLeaveEditor()) return;
    await activateCatalog(scope); showView('catalog');
  }

  // //// 切换观察与登记工作区 [@x380kkm 2026-09-06] ////
  function showView(view) {
    state.view = view;
    element('inventory-workspace').hidden = view !== 'inventory';
    element('catalog-workspace').hidden = view !== 'catalog';
    element('local-codex-button').setAttribute('aria-pressed', String(view === 'inventory' && state.pageScope === 'user'));
    element('project-page-button').setAttribute('aria-pressed', String(view === 'inventory' && state.pageScope !== 'user'));
    element('system-settings-button').setAttribute('aria-pressed', String(view === 'catalog'));
    element('workspace-label').hidden = view !== 'catalog';
    element('refresh-button').disabled = window.manager.readOnly === true;
    element('workspace-path').textContent = view === 'catalog' ? state.catalog || '' : '';
    updateBusyControls();
  }

  // //// 判断各编辑面是否包含尚未保存的内容 [@x380kkm 2026-09-08] ////
  function hasUnsavedChanges() {
    return editorDirty || usageDirty || companionDirty || relationsDirty;
  }

  // //// 汇集各编辑面的退出保护状态 [@x380kkm 2026-09-08] ////
  function updateExitProtection() {
    window.manager.setDirty(hasUnsavedChanges());
  }

  // //// 将包含草稿的页面卸载交给主进程确认 [@x380kkm 2026-09-08] ////
  window.onbeforeunload = (event) => {
    if (!hasUnsavedChanges()) return;
    event.preventDefault();
    event.returnValue = false;
  };
  updateExitProtection();

  // //// 为项目页面选择独立于用户默认的配置位置 [@x380kkm 2026-09-08] ////
  async function chooseInventoryProject(change) {
    if (state.workspace && !change) return state.workspace;
    const result = await unwrap(window.manager.selectWorkspace());
    if (!result) return null;
    state.workspace = result.workspace;
    editor.clear();
    return state.workspace;
  }

  // //// 从选择的声明或继承节点定位来源发布 [@x380kkm 2026-09-06] ////
  function selectedPlugin() {
    const node = state.graph.nodes.find((item) => item.id === state.selectedNode);
    const identity = node?.inheritedDocumentId || node?.documentId || state.selected;
    const owner = state.graph.nodes.find((item) => item.id === identity);
    if (owner?.kind === 'Plugin') return owner.id;
    const relation = state.graph.edges.find((edge) => edge.from === identity && edge.label === 'uses');
    const plugin = state.graph.nodes.find((item) => item.id === relation?.to && item.kind === 'Plugin');
    return plugin?.id || null;
  }

  // //// 显示所选发布可用的管理入口 [@x380kkm 2026-09-06] ////
  function renderContentActions() {
    const available = selectedPlugin() !== null;
    element('usage-button').disabled = !available;
    element('content-button').disabled = !available;
    element('companion-button').disabled = !available;
    updateBusyControls();
  }

  // //// 打开独立使用设置并保留未保存编辑的选择权 [@x380kkm 2026-09-06] ////
  async function openUsage(plugin, scope) {
    if (!canLeaveEditor()) { setStatus('保留当前编辑.'); return; }
    if (editor.isDirty()) editor.clear();
    await usage.open(plugin, scope);
  }

  // //// 将使用设置的计划交给共用编辑器 [@x380kkm 2026-09-06] ////
  function stageUsagePlan(result, scope) {
    showView('catalog');
    state.selected = result.plan.before ? result.plan.id : null;
    state.selectedNode = state.selected;
    editor.showPlan(result, scope);
    renderSelection();
    setStatus('使用设置的差异已生成, 保存时核对原目录基线.');
  }

  // //// 将发生冲突的表单转换成保留基线的 JSON 编辑 [@x380kkm 2026-09-06] ////
  function stageUsageDraft(document, baseline, record) {
    showView('catalog');
    state.selected = record.id || null;
    state.selectedNode = state.selected;
    editor.show(document, baseline, record);
    renderSelection();
    setStatus('使用设置草稿已保留在 JSON 编辑器, 预览时可比较最新登记.');
  }

  // //// 锁定配置入口并保持分类与内容浏览可用 [@x380kkm 2026-09-07] ////
  function updateBusyControls() {
    const busy = state.busy || state.readers > 0;
    document.body.setAttribute('aria-busy', String(busy));
    inventory.setBusy(busy);
    if (!busy) {
      for (const control of busyControls) control.disabled = false;
      busyControls.clear();
      return;
    }
    for (const id of ['local-codex-button', 'project-page-button', 'system-settings-button', 'workspace-button', 'user-catalog-button', 'project-catalog-button', 'project-local-catalog-button',
      'refresh-button', 'new-button', 'import-kind', 'import-button', 'usage-button', 'companion-button', 'preview-button', 'remove-button', 'recover-button',
      'apply-button', 'format-button', 'json-editor']) {
      const control = element(id);
      if (!control.disabled) { busyControls.add(control); control.disabled = true; }
    }
  }

  // //// 并行读取内容并串行执行配置动作 [@x380kkm 2026-09-07] ////
  async function run(action, { readOnly = false } = {}) {
    if (state.busy || !readOnly && state.readers > 0) { setStatus('上一项操作仍在处理, 当前动作未执行. 浏览与搜索仍可使用.'); return; }
    if (readOnly) state.readers += 1; else state.busy = true;
    updateBusyControls();
    element('error-panel').hidden = true;
    setStatus('正在处理...');
    try { return await action(); }
    catch (error) { showError(error); }
    finally {
      if (readOnly) state.readers -= 1; else state.busy = false;
      updateBusyControls();
      if (!state.busy && !state.readers) {
        renderWorkspace(); showView(state.view); renderContentActions();
        if (element('status-message').textContent === '正在处理...') setStatus('就绪.');
      }
    }
  }

  // //// 确认当前编辑是否可以离开 [@x380kkm 2026-09-06] ////
  function canLeaveEditor() {
    return !editor.isDirty() || window.confirm('当前编辑尚未保存. 放弃修改并继续?');
  }

  // //// 显示项目路径和可用动作 [@x380kkm 2026-09-06] ////
  function renderWorkspace() {
    const projectName = state.workspace?.split(/[\\/]/).filter(Boolean).at(-1);
    element('workspace-label').textContent = `${catalogScopeNames[state.scope]}${state.scope !== 'user' && projectName ? ` / ${projectName}` : ''}`;
    if (state.scope !== 'user') element('catalog-project-scopes').open = true;
    element('workspace-button').title = state.workspace || '选择项目局部配置目录';
    element('workspace-path').textContent = state.catalog || '';
    for (const scope of Object.keys(catalogScopeNames)) {
      const button = element(`${scope}-catalog-button`);
      button.setAttribute('aria-pressed', String(state.scope === scope));
      button.disabled = scope !== 'user' && !state.workspace;
    }
    for (const id of ['refresh-button', 'new-button', 'import-kind', 'import-button', 'search-input']) element(id).disabled = !state.catalog;
    updateBusyControls();
  }

  // //// 显示分类下的列表或关系图 [@x380kkm 2026-09-06] ////
  function renderCatalog() {
    const documents = filterDocuments(state.documents, state.kind, state.query);
    const ids = new Set(documents.map((item) => item.id));
    const visibleGraph = graphForDocuments(state.graph, ids, Boolean(state.kind || state.query));
    renderKinds(element('kind-navigation'), state.documents, state.kind, (kind) => {
      state.kind = kind;
      renderCatalog();
    });
    renderDocuments(element('document-list'), documents, state.selected, (id) => run(() => selectDocument(id), { readOnly: true }));
    element('view-title').textContent = state.kind ? `配置源码 / ${state.kind}` : '配置源码';
    element('catalog-caption').textContent = `${catalogScopeNames[state.scope]} / ${documents.length} 项声明 / ${state.documents.length} 项登记`;
    element('list-button').setAttribute('aria-pressed', String(state.mode === 'list'));
    element('graph-button').setAttribute('aria-pressed', String(state.mode === 'graph'));
    element('list-view').hidden = state.mode !== 'list' || documents.length === 0;
    element('graph-view').hidden = state.mode !== 'graph' || visibleGraph.nodes.length === 0;
    element('catalog-empty').hidden = state.mode === 'graph' ? visibleGraph.nodes.length > 0 : documents.length > 0;
    renderDiagnostics(element('catalog-diagnostics'), state.diagnostics);
    if (state.mode === 'graph') graph.render(visibleGraph.nodes, visibleGraph.edges, state.selectedNode);
    element('graph-locate').disabled = !visibleGraph.nodes.some((node) => node.id === state.selectedNode);
    renderEmpty();
    renderContentActions();
  }

  // //// 根据目录和筛选条件显示空状态 [@x380kkm 2026-09-06] ////
  function renderEmpty() {
    const filtered = state.kind || state.query;
    element('empty-title').textContent = filtered ? '没有匹配的声明' : `${catalogScopeNames[state.scope]}中还没有声明`;
    element('empty-description').textContent = filtered ? '调整搜索文本或声明种类后查看内容.' : '添加现有的本地 Skill, AGENTS.md 或声明 JSON.';
    element('empty-action').textContent = filtered ? '清除筛选' : '添加来源';
  }

  // //// 从磁盘刷新目录和声明关系 [@x380kkm 2026-09-06] ////
  async function refresh() {
    const version = ++catalogVersion, scope = state.scope;
    let snapshot;
    try { snapshot = await request('catalog.snapshot', { scope }); }
    catch (error) { if (version === catalogVersion && scope === state.scope) throw error; return; }
    if (version !== catalogVersion || scope !== state.scope) return;
    Object.assign(state, { workspace: snapshot.workspace, catalog: snapshot.catalog, scope: snapshot.scope, documents: snapshot.documents, graph: snapshot.graph, diagnostics: snapshot.diagnostics });
    if (state.selected && !state.documents.some((item) => item.id === state.selected) && !editor.isDirty()) {
      state.selected = null;
      state.selectedNode = null;
      editor.clear();
    }
    renderWorkspace();
    renderCatalog();
    setStatus('目录已从磁盘读取.');
  }

  // //// 取得登记声明对应的当前图节点 [@x380kkm 2026-09-06] ////
  function selectionNode(id) {
    const matches = (node) => registeredDocumentId(node) === id;
    return state.graph.nodes.find((node) => node.id === state.selectedNode && matches(node))?.id
      || state.graph.nodes.find((node) => node.id === id && matches(node))?.id
      || state.graph.nodes.find(matches)?.id || null;
  }

  // //// 同步列表和关系图的选择状态 [@x380kkm 2026-09-06] ////
  function renderSelection() {
    renderDocuments(element('document-list'), filterDocuments(state.documents, state.kind, state.query), state.selected, (selected) => run(() => selectDocument(selected), { readOnly: true }));
    graph.select(state.selectedNode);
    const visible = graphForDocuments(state.graph, new Set(filterDocuments(state.documents, state.kind, state.query).map((item) => item.id)), Boolean(state.kind || state.query));
    element('graph-locate').disabled = !visible.nodes.some((node) => node.id === state.selectedNode);
    renderContentActions();
  }

  // //// 读取声明并更新编辑基线 [@x380kkm 2026-09-06] ////
  async function readDocument(id) {
    const version = ++documentVersion, scope = state.scope, revision = editorRevision;
    let result;
    try { result = await request('document.read', { id, scope }); }
    catch (error) { if (version === documentVersion && scope === state.scope) throw error; return false; }
    if (version !== documentVersion || scope !== state.scope || revision !== editorRevision) return false;
    state.selected = id;
    state.selectedNode = selectionNode(id);
    editor.show(result.document, result.baseline, { ...state.documents.find((item) => item.id === id), id, scope: state.scope });
    renderSelection();
    setStatus('声明已读取.');
    return true;
  }

  // //// 选择声明并保留同一对象的当前编辑 [@x380kkm 2026-09-06] ////
  async function selectDocument(id) {
    if (state.selected === id) { documentVersion += 1; setStatus('当前声明的编辑已保留.'); return true; }
    if (!canLeaveEditor()) { setStatus('保留当前编辑.'); return false; }
    return readDocument(id);
  }

  // //// 将图节点映射到登记声明或只读引用 [@x380kkm 2026-09-06] ////
  async function selectGraphNode(id) {
    const node = state.graph.nodes.find((item) => item.id === id);
    if (!node) return;
    const documentId = registeredDocumentId(node);
    if (documentId === null) {
      if (!canLeaveEditor()) { setStatus('保留当前编辑.'); return; }
      documentVersion += 1;
      state.selected = null;
      state.selectedNode = id;
      editor.showReference(node);
      renderSelection();
      setStatus(node.inherited ? '当前节点来自用户级目录. 可在用户级内容中编辑.' : '当前节点是未登记引用.');
      return;
    }
    if (await selectDocument(documentId)) {
      state.selectedNode = id;
      renderSelection();
    }
  }

  // //// 通过原生对话框切换项目目录 [@x380kkm 2026-09-08] ////
  async function chooseWorkspace() {
    const result = await unwrap(window.manager.selectWorkspace());
    if (!result) { setStatus('保留当前项目.'); return; }
    state.workspace = result.workspace;
    await activateCatalog('project');
  }

  // //// 切换明确归属的声明目录 [@x380kkm 2026-09-06] ////
  async function activateCatalog(scope) {
    catalogVersion += 1; documentVersion += 1;
    state.scope = scope;
    state.selected = null;
    state.selectedNode = null;
    state.kind = '';
    state.query = '';
    state.documents = [];
    state.graph = { nodes: [], edges: [] };
    state.diagnostics = [];
    graph.clear();
    element('search-input').value = '';
    editor.clear();
    renderWorkspace();
    renderCatalog();
    await refresh();
  }

  // //// 将选定来源载入可编辑的导入声明 [@x380kkm 2026-09-06] ////
  async function importSource() {
    if (!canLeaveEditor()) { setStatus('保留当前编辑.'); return; }
    const kind = element('import-kind').value || undefined;
    const result = await unwrap(window.manager.importFile(kind, state.scope));
    if (!result) { setStatus('保留当前编辑.'); return; }
    state.selected = null;
    state.selectedNode = null;
    editor.show(result.document, result.baseline, { scope: state.scope, path: state.catalog });
    renderDiagnostics(element('catalog-diagnostics'), result.diagnostics);
    graph.select(null);
    setStatus('来源已读取. 预览并保存后加入登记.');
  }

  // //// 保存后重新读取当前磁盘内容 [@x380kkm 2026-09-06] ////
  async function onApplied(result) {
    state.selected = result.document ? result.id : null;
    state.selectedNode = null;
    editor.clear();
    await refresh();
    if (result.document) await readDocument(result.id);
  }

  // //// 同步用户确认的合并对象选择 [@x380kkm 2026-09-06] ////
  function onRecovered(id) {
    state.selected = id;
    state.selectedNode = id === null ? null : selectionNode(id);
    renderSelection();
  }

  element('workspace-button').addEventListener('click', () => run(chooseWorkspace));
  element('usage-button').addEventListener('click', () => run(async () => { const plugin = selectedPlugin(); if (plugin) await openUsage(plugin, state.scope); }));
  element('content-button').addEventListener('click', () => run(async () => { const plugin = selectedPlugin(); if (plugin) await contentPreview.open(plugin, state.scope); }, { readOnly: true }));
  element('companion-button').addEventListener('click', () => run(async () => {
    const plugin = selectedPlugin();
    if (!plugin || !canLeaveEditor()) return;
    if (editor.isDirty()) editor.clear();
    await companions.open(plugin, state.scope);
  }));
  for (const scope of Object.keys(catalogScopeNames)) element(`${scope}-catalog-button`).addEventListener('click', () => run(async () => {
    if (state.scope === scope) { setStatus('保留当前目录与编辑.'); return; }
    if (canLeaveEditor()) await activateCatalog(scope);
    else setStatus('保留当前编辑.');
  }));
  element('local-codex-button').addEventListener('click', () => run(async () => {
    showView('inventory');
    await inventory.openUser();
  }));
  element('project-page-button').disabled = window.manager.readOnly === true;
  element('project-page-button').addEventListener('click', () => run(async () => { showView('inventory'); await inventory.openProject(); }));
  element('system-settings-button').addEventListener('click', () => run(() => settings.open(state.pageScope)));
  element('refresh-button').addEventListener('click', () => run(async () => {
    if (state.view === 'inventory') { await inventory.refresh(true); return; }
    await refresh();
    if (state.selected && !editor.isDirty()) await readDocument(state.selected);
  }, { readOnly: true }));
  element('import-button').addEventListener('click', () => run(importSource));
  element('new-button').addEventListener('click', () => run(() => {
    if (!canLeaveEditor()) return;
    state.selected = null;
    state.selectedNode = null;
    editor.show(null, null, { scope: state.scope, path: state.catalog });
    graph.select(null);
    setStatus('输入完整声明后预览变更.');
  }));
  element('search-input').addEventListener('input', (event) => { state.query = event.target.value; renderCatalog(); });
  for (const mode of ['list', 'graph']) element(`${mode}-button`).addEventListener('click', () => { state.mode = mode; renderCatalog(); });
  element('graph-in').addEventListener('click', () => graph.zoom(1 / 1.25));
  element('graph-out').addEventListener('click', () => graph.zoom(1.25));
  element('graph-fit').addEventListener('click', () => graph.fit());
  element('graph-locate').addEventListener('click', () => { if (state.selectedNode) graph.reveal(state.selectedNode); });
  element('empty-action').addEventListener('click', () => {
    if (state.kind || state.query) {
      state.kind = ''; state.query = ''; element('search-input').value = ''; renderCatalog();
      return;
    }
    return run(importSource);
  });
  element('error-close').addEventListener('click', () => { element('error-panel').hidden = true; });
  element('theme-button').addEventListener('click', () => {
    const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('theme', theme);
    element('theme-button').textContent = theme === 'dark' ? '浅色' : '深色';
  });
  const theme = localStorage.getItem('theme') || 'light';
  document.documentElement.dataset.theme = theme;
  element('theme-button').textContent = theme === 'dark' ? '浅色' : '深色';
  renderWorkspace();
  renderCatalog();
  showView('inventory');
  run(async () => {
    const result = await unwrap(window.manager.getContext());
    state.workspace = result.workspace;
    renderWorkspace();
    if (!window.manager.readOnly) await request('host.initialize', { scope: 'user' });
    await inventory.refresh();
    showView('inventory');
  });
}
