// audience: internal
// # hook-editor
// Hook 编辑保存一个事件匹配组及完整处理项, 执行由宿主负责.

import { refreshAfterSave, renderDiagnostics, request, textElement } from './view-utils.mjs';

const hookPoint = 'hook.x380kkm/lifecycle';
const events = ['SessionStart', 'SessionEnd', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'PermissionRequest', 'PreCompact', 'PostCompact', 'SubagentStart', 'SubagentStop', 'Stop', 'Interrupt'];

// //// 创建与标签对应的 Hook 字段 [@x380kkm 2026-09-07] ////
function field(label, input) {
  const row = textElement('label', '', 'module-field'); row.append(textElement('span', label), input); return row;
}

// //// 编辑独立版本的生命周期处理配置 [@x380kkm 2026-09-07] ////
export function createHookEditor({ run, onSaved, onDirty, setStatus }) {
  const dialog = textElement('dialog', '', 'hook-editor-dialog'); dialog.setAttribute('aria-label', 'Hook 配置'); document.body.append(dialog);
  let name, event, matcher, handlers, feedback, save, initial, draft, baseline, scope, plan;
  const values = () => [name.value, event.value, matcher.value, handlers.value];
  const dirty = () => JSON.stringify(values()) !== initial;
  const canLeave = () => !dirty() || window.confirm('Hook 配置尚未保存. 放弃修改?');

  // //// 核对处理项并生成静态事件配置 [@x380kkm 2026-09-07] ////
  function payload() {
    const selected = JSON.parse(handlers.value);
    if (!name.value.trim() || !event.value || !Array.isArray(selected) || !selected.length) throw new Error('请填写名称, 事件和至少一个处理项.');
    for (const handler of selected) {
      if (!handler || !['command', 'mcp_tool'].includes(handler.type)) throw new Error('处理项类型为 command 或 mcp_tool.');
      if (handler.type === 'command' && !handler.command) throw new Error('command 处理项需要 command 命令, 可另设 commandWindows.');
      if (handler.type === 'mcp_tool' && (!handler.server || !handler.tool)) throw new Error('mcp_tool 处理项需要 server 与 tool.');
    }
    return { name: name.value.trim(), event: event.value, ...(matcher.value ? { matcher: matcher.value } : {}), handlers: selected };
  }

  // //// 执行保存并保留无效输入的草稿 [@x380kkm 2026-09-07] ////
  function execute(action) {
    run(async () => {
      dialog.inert = true;
      try { await action(); }
      catch (error) { feedback.textContent = error.message; }
      finally { dialog.inert = false; }
    });
  }

  // //// 打开新 Hook 或所选作用域中的独立声明 [@x380kkm 2026-09-07] ////
  async function open(item, selectedScope) {
    scope = selectedScope; plan = null; dialog.replaceChildren();
    const record = item ? await request('document.read', { id: item.management.documentId, scope }) : null;
    baseline = record?.baseline || null;
    draft = structuredClone(record?.document || { apiVersion: 'manager.x380kkm/v1', kind: 'Plugin', id: `plugin:hook/${crypto.randomUUID()}`,
      release: { version: 'local' }, metadata: {}, contributions: [{ id: 'hook', point: hookPoint, contract: { id: hookPoint, range: '^1.0.0' }, payload: {} }] });
    if (draft.contributions.length !== 1 || draft.contributions[0].source) throw new Error('此内容由外部来源维护. 可以新建独立 Hook 配置.');
    const current = draft.contributions[0].payload;
    const heading = textElement('div', '', 'dialog-heading'), close = textElement('button', '取消');
    close.addEventListener('click', () => { if (canLeave()) dialog.close(); }); heading.append(textElement('h2', item ? '编辑 Hook' : '新建 Hook'), close);
    const body = textElement('div', '', 'hook-editor-body');
    name = document.createElement('input'); name.value = draft.metadata?.name || current.name || item?.name || ''; name.setAttribute('aria-label', 'Hook 名称');
    event = document.createElement('select'); event.setAttribute('aria-label', 'Hook 事件');
    for (const value of ['', ...events]) { const option = textElement('option', value || '选择生命周期事件'); option.value = value; event.append(option); } event.value = current.event || '';
    matcher = document.createElement('input'); matcher.value = current.matcher || ''; matcher.setAttribute('aria-label', 'Hook 匹配条件'); matcher.placeholder = '可选, 使用宿主支持的匹配表达式';
    handlers = document.createElement('textarea'); handlers.rows = 12; handlers.value = JSON.stringify(current.handlers || [], null, 2); handlers.setAttribute('aria-label', 'Hook 处理项');
    body.append(field('名称', name), field('生命周期事件', event), field('匹配条件', matcher), field('处理项 / JSON 数组', handlers));
    body.append(textElement('p', 'command 使用 command, 可另设 commandWindows; mcp_tool 使用 server, tool 与可选 input. 支持多个处理项, 配置写入后由宿主审阅信任并执行.', 'card-hint'));
    feedback = textElement('div', '', 'settings-feedback'); feedback.setAttribute('aria-live', 'polite');
    const actions = textElement('div', '', 'dialog-actions'), preview = textElement('button', '预览配置'); save = textElement('button', '保存 Hook', 'primary'); save.disabled = true;
    const changed = () => { plan = null; save.disabled = true; onDirty(dirty()); };
    for (const input of [name, event, matcher, handlers]) input.addEventListener('input', changed);
    preview.addEventListener('click', () => execute(async () => {
      draft.contributions[0].payload = payload(); draft.metadata ??= {}; draft.metadata.name = name.value.trim();
      const result = await request('document.preview', { document: draft, baseline, scope }); plan = result.plan;
      const diagnostics = textElement('div'); renderDiagnostics(diagnostics, result.diagnostics);
      feedback.replaceChildren(textElement('p', `保存 ${draft.contributions[0].payload.handlers.length} 个处理项. 启用状态单独设置.`), diagnostics);
      save.disabled = false;
    }));
    save.addEventListener('click', () => execute(async () => {
      if (!plan) return;
      await request('document.apply', { plan }); initial = JSON.stringify(values()); onDirty(false); dialog.close();
      await refreshAfterSave(onSaved, setStatus, 'Hook 声明已保存.');
    }));
    const buttons = textElement('div', '', 'button-group'); buttons.append(preview, save);
    actions.append(textElement('span', scope === 'user' ? '用户级 Hook 内容' : '项目个人 Hook 内容'), buttons);
    dialog.append(heading, body, feedback, actions); initial = JSON.stringify(values()); onDirty(false); dialog.showModal();
  }
  dialog.addEventListener('cancel', (event) => { if (!canLeave()) event.preventDefault(); }); dialog.addEventListener('close', () => onDirty(false));
  return { open };
}
