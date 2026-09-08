// audience: internal
// # codex-inventory-view
// 卡片与列表共用来源筛选和选择. 关闭内容的显示设置独立于宿主启用状态.

import { element, request, textElement, unwrap } from './view-utils.mjs';
import { categories, inventoryModel, matchesGroup } from './inventory-model.mjs';
import { groupDisabled, visibleInventory } from './inventory-visibility.mjs';
import { renderGroupList, selectGroupRow } from './inventory-list.mjs';
import { renderInventoryCards } from './inventory-cards.mjs';
import { renderInventoryInspector } from './inventory-inspector.mjs';
import { createRelationsDialog } from './relations-dialog.mjs';
import { createStatisticsDialog } from './statistics-dialog.mjs';
import { createRuleEditor } from './rule-editor.mjs';

// //// 管理不同布局中的同一组内容卡片 [@x380kkm 2026-09-07] ////
export function createInventoryView({ run, setStatus, onProject, onScopeChanged, onDirty, onSettings, onSource, onModule, onHook }) {
  const empty = inventoryModel({ items: [], edges: [] });
  const state = { raw: empty, model: empty, category: 'modules', query: '', selected: null, expanded: new Set(), mode: 'cards', showDisabled: false, scope: 'user', busy: false, hiddenCount: 0, expansion: 0 };
  const official = element('inventory-official');
  const inspector = element('inventory-inspector');
  const relations = createRelationsDialog({ run, setStatus, onSaved: refresh, onDirty });
  const statistics = createStatisticsDialog({ run, setStatus });
  let initialView = true;
  let refreshVersion = 0, scopeVersion = 0, filtered = null;
  const navigationButtons = new Map(), busyControls = new Set(), renderedLists = new Map();
  const ruleEditor = createRuleEditor({ run, setStatus, onSaved: refresh, onDirty });

  // //// 在后台动作期间保留浏览并锁定配置入口 [@x380kkm 2026-09-07] ////
  function setBusy(value) {
    state.busy = value;
    if (!value) {
      for (const control of busyControls) control.disabled = false;
      busyControls.clear();
      return;
    }
    const selectors = 'select, .card-controls input, .card-controls button, .inventory-section > button, [data-settings-entry]';
    for (const control of element('inventory-workspace').querySelectorAll(selectors)) {
      if (!control.disabled) { busyControls.add(control); control.disabled = true; }
    }
    for (const id of ['inventory-new-module', 'inventory-new-hook', 'inventory-change-project', 'inventory-export']) {
      const control = element(id);
      if (!control.disabled) { busyControls.add(control); control.disabled = true; }
    }
  }

  // //// 将模块整体使用设置保存到当前配置层 [@x380kkm 2026-09-07] ////
  async function configureModule(item, stateValue) {
    const usage = await request('usage.describe', { plugin: item.details.documentId, scope: state.scope });
    const defaultId = `binding:${state.scope}/${item.details.pluginId}`;
    const baseline = usage.bindings.find((binding) => binding.id === defaultId) || null;
    if (stateValue === 'inherit' && baseline) {
      const result = await request('document.preview_remove', { id: baseline.id, baseline, scope: state.scope });
      await request('document.apply', { plan: result.plan });
    } else if (stateValue !== 'inherit') {
      const result = await request('usage.preview', { plugin: item.details.documentId, scope: state.scope, baseline, settings: { state: stateValue } });
      await request('document.apply', { plan: result.plan });
    }
    await refresh(); setStatus('模块使用设置已保存.');
  }

  // //// 在当前页面的配置层快捷调整启用状态 [@x380kkm 2026-09-07] ////
  async function configure(id, value) {
    const baseline = state.raw.items.get(id)?.management?.configBaseline;
    await request('card.configure', { id, state: value, scope: state.scope, baseline });
    await refresh();
    setStatus(`${state.scope === 'user' ? '用户默认' : '个人项目覆盖'}已更新.`);
  }

  // //// 发布或收回项目卡片并同步受影响关系 [@x380kkm 2026-09-07] ////
  async function share(id, value) {
    const description = await request('card.describe', { id, scope: 'project-local' });
    let result;
    try { result = await request('card.set_shared', { id, shared: value, baseline: description.sharingBaseline }); }
    catch (error) { await refresh(); throw error; }
    await refresh();
    setStatus(value ? '项目共享文件已更新, 依赖按两端共享状态同步.' : '项目共享项已移出, 个人设置与关系保留.');
    if (result.warnings?.length) {
      element('inventory-diagnostics').hidden = false;
      element('inventory-diagnostics-title').textContent = '共享来源提示';
      element('inventory-diagnostics-body').replaceChildren(...result.warnings.map((warning) => textElement('p', warning.message || String(warning))));
    }
  }

  // //// 切换到用户默认设置页面 [@x380kkm 2026-09-07] ////
  async function openUser() { scopeVersion += 1; state.scope = 'user'; state.selected = null; await refresh(); }

  // //// 选择实际项目后合成共享与个人覆盖 [@x380kkm 2026-09-07] ////
  async function openProject(change = false) {
    const version = ++scopeVersion;
    refreshVersion += 1;
    const project = await onProject(change);
    if (version !== scopeVersion) return;
    if (!project) { setStatus('保留当前页面.'); return; }
    state.scope = 'project-local'; state.selected = null;
    await refresh();
    if (version === scopeVersion && state.raw.snapshot.hostControl?.enabled === true && !window.manager.readOnly) await inspectProject();
  }

  // //// 只读检查当前项目的宿主差异并提供预览入口 [@x380kkm 2026-09-08] ////
  async function inspectProject() {
    const version = scopeVersion, inventoryVersion = refreshVersion;
    let result;
    try { result = await request('host.inspect', { scope: 'project-local' }); }
    catch (error) { if (version === scopeVersion && inventoryVersion === refreshVersion && state.scope === 'project-local') throw error; return; }
    if (version !== scopeVersion || inventoryVersion !== refreshVersion || state.scope !== 'project-local') return;
    const hint = element('inventory-scope-hint');
    hint.hidden = result.status === 'unchanged';
    if (hint.hidden) return;
    const messages = { pending: '项目宿主配置有待应用的变化.', blocked: '项目宿主配置应用受阻.',
      uninitialized: '此项目的原配置恢复点尚未建立.', disabled: '接管已关闭, 当前宿主文件保持不变.' };
    const message = [messages[result.status], ...(result.diagnostics || []).map((note) => note.message)].join(' ');
    hint.dataset.state = result.status === 'disabled' ? 'off' : 'unknown';
    hint.replaceChildren(textElement('span', message)); setStatus(message);
    if (result.status !== 'disabled') {
      const settings = textElement('button', '配置接管与备份', 'text-button'); settings.dataset.settingsEntry = '';
      settings.addEventListener('click', () => onSettings('project-local')); hint.append(settings);
      if (state.busy) setBusy(true);
    }
  }

  // //// 在统一筛选中取得一个来源区域的卡片 [@x380kkm 2026-09-07] ////
  function filteredGroups(origin) {
    if (!filtered || filtered.model !== state.model || filtered.category !== state.category || filtered.query !== state.query) {
      const terms = state.query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
      const groups = (state.model.categoryGroups.get(state.category) || state.model.groups).filter((group) => matchesGroup(state.model, group, '', terms));
      filtered = { model: state.model, category: state.category, query: state.query,
        user: groups.filter((group) => group.origin === 'user'), official: groups.filter((group) => group.origin === 'official') };
    }
    return filtered[origin];
  }

  // //// 用个人卡片数量建立分类入口 [@x380kkm 2026-09-07] ////
  function renderNavigation() {
    if (navigationButtons.size) {
      for (const [id, { button, count }] of navigationButtons) {
        const current = state.category === id ? 'page' : 'false';
        if (button.getAttribute('aria-current') !== current) button.setAttribute('aria-current', current);
        const value = String(state.model.categoryCounts.get(id) || 0);
        if (count.textContent !== value) count.textContent = value;
      }
      return;
    }
    const navigation = element('inventory-kinds'); navigation.replaceChildren();
    let section = '';
    for (const category of categories) {
      if (category.section !== section) {
        section = category.section;
        if (section !== 'modules') navigation.append(textElement('h2', { content: '基础内容', system: '系统' }[section], 'navigation-section-title'));
      }
      const count = textElement('span', state.model.categoryCounts.get(category.id) || 0, 'kind-count');
      const button = textElement('button', '', 'kind-item');
      button.append(textElement('span', category.shortName || category.name), count);
      navigationButtons.set(category.id, { button, count });
      button.setAttribute('aria-current', state.category === category.id ? 'page' : 'false');
      button.addEventListener('click', () => { initialView = false; state.category = category.id; state.selected = null; render(); });
      navigation.append(button);
      if (category.id === 'sources') {
        const settings = textElement('button', '配置接管与备份', 'kind-item');
        settings.dataset.settingsEntry = '';
        settings.addEventListener('click', () => onSettings(state.scope)); navigation.append(settings);
        const source = textElement('button', '配置源码', 'kind-item');
        source.dataset.settingsEntry = '';
        source.addEventListener('click', () => onSource(state.scope)); navigation.append(source);
      }
    }
  }

  // //// 在列表中展开原始组成项 [@x380kkm 2026-09-07] ////
  function toggleGroup(id) {
    if (state.expanded.has(id)) state.expanded.delete(id); else state.expanded.add(id);
    state.expansion += 1;
    renderLists();
    element('inventory-workspace').querySelector(`[data-toggle-id="${CSS.escape(id)}"]`)?.focus({ preventScroll: true });
  }

  // //// 渲染卡片或紧凑列表并保留官方折叠状态 [@x380kkm 2026-09-07] ////
  function renderLists() {
    const personal = filteredGroups('user'), bundled = filteredGroups('official');
    const category = categories.find((value) => value.id === state.category);
    element('inventory-page-title').textContent = category?.name || '内容库';
    const caption = element('inventory-caption');
    caption.hidden = !state.query.trim();
    caption.textContent = `找到 ${personal.length + bundled.length} 项`;
    const options = { expanded: state.expanded, query: state.query, onToggle: toggleGroup, onSelect: select,
      showKinds: !state.category || ['library', 'sources'].includes(state.category),
      onModule: (item) => onModule(item, state.scope),
      onConfigureModule: (item, value) => run(() => configureModule(item, value)),
      readOnly: window.manager.readOnly, scope: state.scope,
      onConfigure: (id, value) => run(() => configure(id, value)), onShare: (id, value) => run(() => share(id, value)),
      onRelations: (id) => run(() => relations.open(id, state.scope)) };
    const renderer = state.mode === 'list' ? renderGroupList : renderInventoryCards;
    for (const [id, groups] of [['inventory-list', personal], ['inventory-official-list', official.open ? bundled : null]]) {
      const previous = renderedLists.get(id);
      if (previous?.groups === groups && previous.model === state.model && previous.mode === state.mode && previous.expansion === state.expansion) continue;
      if (groups) renderer(element(id), state.model, groups, options); else element(id).replaceChildren();
      renderedLists.set(id, { groups, model: state.model, mode: state.mode, expansion: state.expansion });
    }
    official.hidden = !bundled.length;
    element('inventory-official-count').textContent = `${bundled.length} 项${state.query.trim() ? '匹配' : ''}`;
    element('inventory-empty').hidden = personal.length > 0;
    element('inventory-empty').textContent = state.category === 'modules' ? `模块将规则, Skills, Hooks 和 Tools 组合为一项可使用的配置.${window.manager.readOnly ? ' 可在桌面应用中创建模块.' : ' 点击新建模块选择成员和职责.'}` : '没有匹配的个人内容.';
    element('inventory-new-module').hidden = state.category !== 'modules' || window.manager.readOnly;
    element('inventory-new-hook').hidden = state.category !== 'hooks' || window.manager.readOnly;
    element('inventory-result-count').textContent = !state.showDisabled && state.hiddenCount ? `已隐藏 ${state.hiddenCount} 项关闭内容` : '';
    selectGroupRow(element('inventory-list'), state.model, state.selected);
    selectGroupRow(element('inventory-official-list'), state.model, state.selected);
    if (state.busy) setBusy(true);
  }

  // //// 在各视图中打开同一内容详情 [@x380kkm 2026-09-07] ////
  function renderInspector() {
    const visibleItems = new Set([...filteredGroups('user'), ...(official.open ? filteredGroups('official') : [])].flatMap((group) => group.itemIds));
    renderInventoryInspector(inspector, state.model, state.selected, {
      readOnly: window.manager.readOnly, onSelect: select, onClose: () => select(null),
      onEditRule: (item) => run(() => ruleEditor.open(item)),
      onEditHook: (item) => onHook(item, state.scope),
      onModule: (item) => onModule(item, state.scope),
      onConfigureModule: (item, value) => run(() => configureModule(item, value)),
      visibleItems,
      scope: state.scope, onConfigure: (id, value) => run(() => configure(id, value)), onShare: (id, value) => run(() => share(id, value)),
      onRelations: (id) => run(() => relations.open(id, state.scope)),
    });
    element('inventory-workspace').classList.toggle('has-inspector', !inspector.hidden);
    if (state.busy) setBusy(true);
  }

  // //// 保持卡片选择与列表的同步 [@x380kkm 2026-09-07] ////
  function select(id) {
    const previous = state.selected;
    const group = state.model.byId.get(id) || state.model.byId.get(state.model.itemGroups.get(id));
    if (id && !group) return;
    state.selected = id;
    if (group && !matchesGroup(state.model, group, state.category, state.query)) {
      state.category = ''; state.query = ''; element('inventory-search').value = '';
      renderNavigation(); renderLists();
    }
    if (group?.origin === 'official' && !official.open) { official.open = true; renderLists(); }
    if (group && group.itemIds.length > 1 && id && !state.model.byId.has(id) && !state.expanded.has(group.id)) { state.expanded.add(group.id); state.expansion += 1; renderLists(); }
    selectGroupRow(element('inventory-list'), state.model, id); selectGroupRow(element('inventory-official-list'), state.model, id);
    renderInspector();
    if (!id && previous) element('inventory-list').querySelector(`[data-select-id="${CSS.escape(previous)}"]`)?.focus({ preventScroll: true });
    else inspector.scrollTop = 0;
  }

  // //// 同步筛选与布局入口的当前状态 [@x380kkm 2026-09-07] ////
  function render() {
    const group = state.model.byId.get(state.selected) || state.model.byId.get(state.model.itemGroups.get(state.selected));
    if (!group || !matchesGroup(state.model, group, state.category, state.query) || (group.origin === 'official' && !official.open)) state.selected = null;
    renderNavigation(); renderLists(); renderInspector();
    for (const mode of ['cards', 'list']) element(`inventory-${mode}-button`).setAttribute('aria-pressed', String(mode === state.mode));
  }

  // //// 读取本机卡片并保留显式来源状态 [@x380kkm 2026-09-07] ////
  async function refresh(fresh = false) {
    const version = ++refreshVersion, scope = state.scope;
    element('inventory-workspace').setAttribute('aria-busy', 'true');
    if (!state.raw.groups.length) element('inventory-empty').textContent = '正在读取本机内容...';
    let snapshot;
    try { snapshot = await request('card.inventory', { scope, ...(fresh ? { refresh: true } : {}) }); }
    catch (error) {
      if (version !== refreshVersion || scope !== state.scope) return;
      if (!state.raw.groups.length) element('inventory-empty').textContent = '读取未完成. 请查看操作反馈, 或重新刷新.';
      throw error;
    } finally {
      if (version === refreshVersion) element('inventory-workspace').setAttribute('aria-busy', 'false');
    }
    if (version !== refreshVersion || scope !== state.scope) return;
    state.raw = inventoryModel(snapshot);
    state.model = visibleInventory(state.raw, state.showDisabled);
    state.hiddenCount = state.raw.groups.filter((group) => groupDisabled(state.raw, group)).length;
    if (initialView) {
      if (!snapshot.items.some((item) => item.kind === 'module')) state.category = 'library';
      initialView = false;
    }
    const project = snapshot.workspace;
    element('inventory-scope-label').hidden = state.scope === 'user';
    element('inventory-scope-label').textContent = project?.split(/[\\/]/).at(-1) || '项目';
    const hint = element('inventory-scope-hint');
    hint.dataset.state = snapshot.hostControl?.available === false ? 'unknown' : snapshot.hostControl?.enabled === false ? 'off' : 'on';
    hint.hidden = hint.dataset.state === 'on';
    hint.textContent = snapshot.hostControl?.available === false ? '接管状态需要检查'
      : snapshot.hostControl?.enabled === false ? '接管已关闭 / 当前宿主文件保持不变'
        : state.scope === 'user' ? '接管已开启 / 保存后应用到本机文件' : '继承用户设置 / 当前修改保存在项目个人层';
    element('inventory-change-project').hidden = state.scope === 'user' || window.manager.readOnly === true;
    onScopeChanged?.(state.scope);
    element('inventory-root').textContent = snapshot.root;
    element('inventory-mode').textContent = window.manager.readOnly ? '只读快照' : '本地读取';
    element('inventory-captured-at').textContent = new Date(snapshot.scannedAt).toLocaleString('zh-CN', { hour12: false });
    element('inventory-coverage').textContent = `${snapshot.coverage?.filesRead || 0} 份来源文件 / ${snapshot.items.length} 项原始记录`;
    const notes = snapshot.diagnostics || [];
    element('inventory-diagnostics').hidden = !notes.length;
    element('inventory-diagnostics-title').textContent = `${notes.length} 项读取提示`;
    element('inventory-diagnostics-body').replaceChildren(...notes.map((note) => textElement('p', `${note.message || String(note)} ${note.path || ''}`)));
    render(); setStatus(window.manager.readOnly ? '只读快照. 配置修改在桌面应用中完成.' : '本机内容已读取.');
    if (fresh && state.scope === 'project-local' && snapshot.hostControl?.enabled === true && !window.manager.readOnly) await inspectProject();
  }

  element('inventory-search').addEventListener('input', (event) => { state.query = event.target.value; render(); });
  element('inventory-show-disabled').addEventListener('change', (event) => { state.showDisabled = event.target.checked; state.model = visibleInventory(state.raw, state.showDisabled); render(); });
  for (const mode of ['cards', 'list']) element(`inventory-${mode}-button`).addEventListener('click', () => { state.mode = mode; render(); });
  official.addEventListener('toggle', render);
  element('inventory-export').hidden = !window.manager.exportSnapshot;
  element('inventory-change-project').addEventListener('click', () => run(() => openProject(true)));
  element('inventory-statistics').addEventListener('click', () => run(statistics.open, { readOnly: true }));
  element('inventory-new-module').addEventListener('click', () => onModule(null, state.scope));
  element('inventory-new-hook').addEventListener('click', () => onHook(null, state.scope));
  element('inventory-export').addEventListener('click', () => run(async () => {
    if (await unwrap(window.manager.exportSnapshot())) setStatus('只读快照已更新.');
  }));
  render();
  return { refresh, openUser, openProject, setBusy };
}
