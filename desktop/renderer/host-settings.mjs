// audience: internal
// # host-settings
// 接管开关与备份恢复分别操作, 文件写入经当前预览确认后提交.

import { request, textElement } from './view-utils.mjs';

// //// 提供宿主接管状态, 文件预览和备份恢复 [@x380kkm 2026-09-07] ////
export function createHostSettings({ run, onSaved, onSource, setStatus }) {
  const dialog = textElement('dialog', '', 'host-settings-dialog');
  dialog.setAttribute('aria-label', '配置接管与备份'); document.body.append(dialog);
  let scope = 'user', current, body, feedback, previewBox, confirmation, preview = null;

  // //// 在本地反馈中保留失败原因 [@x380kkm 2026-09-07] ////
  function execute(action) {
    run(async () => {
      dialog.inert = true;
      try { await action(); }
      catch (error) { feedback.textContent = error.message; setStatus(error.message); }
      finally { dialog.inert = false; }
    });
  }

  // //// 展示覆盖或恢复所影响的确切文件 [@x380kkm 2026-09-07] ////
  function showPreview(result) {
    preview = result; previewBox.replaceChildren(); confirmation.hidden = true;
    for (const note of result.diagnostics || []) previewBox.append(textElement('p', note.message || String(note), 'diagnostic'));
    if (!result.planId) return;
    previewBox.append(textElement('h3', result.backupId ? '恢复预览' : '应用预览'));
    for (const file of result.files) {
      const details = textElement('details', '', 'inventory-disclosure');
      details.append(textElement('summary', `${file.operation} ${file.name} / ${file.beforeBytes} → ${file.afterBytes} 字节`), textElement('pre', file.diff));
      previewBox.append(details);
    }
    if (!result.files.length) previewBox.append(textElement('p', result.backupId ? '文件内容与所选备份一致, 确认后关闭接管.'
      : result.ownershipChanged ? '文件内容保持不变, 确认后更新管理归属.' : '当前没有待应用的文件变化.'));
    confirmation.textContent = result.backupId ? '恢复所选备份' : '应用到文件';
    confirmation.hidden = !result.files.length && !result.backupId && !result.ownershipChanged;
    previewBox.scrollIntoView({ block: 'nearest' });
  }

  // //// 重新读取状态并保持备份选择明确 [@x380kkm 2026-09-07] ////
  async function load() {
    current = await request('host.status', { scope });
    if (current.available === false) throw new Error(current.reason);
    preview = null; render();
  }

  // //// 显示搬移涉及的配置路径与可选择用户来源 [@x380kkm 2026-09-08] ////
  function showRelocation(result) {
    preview = { relocationPlan: result.plan }; previewBox.replaceChildren();
    previewBox.append(textElement('h3', '项目重新关联预览'),
      textElement('p', `${result.plan.oldWorkspace} → ${result.plan.workspace}`));
    for (const warning of result.warnings || []) previewBox.append(textElement('p', warning, 'diagnostic'));
    const selected = new Set(result.plan.sourceIds);
    for (const candidate of result.sourceCandidates || []) {
      const label = textElement('label', '', 'host-toggle');
      const check = document.createElement('input'); check.type = 'checkbox'; check.checked = candidate.selected;
      check.addEventListener('change', () => {
        if (check.checked) selected.add(candidate.documentId); else selected.delete(candidate.documentId);
        preview = null; confirmation.hidden = true;
      });
      label.append(check, textElement('span', candidate.name)); previewBox.append(label);
    }
    for (const change of result.changes || []) {
      const details = textElement('details', '', 'inventory-disclosure');
      details.append(textElement('summary', `${change.scope} / ${change.catalog}`), textElement('pre', JSON.stringify(change.documents, null, 2)));
      previewBox.append(details);
    }
    const refresh = textElement('button', '重新预览');
    refresh.addEventListener('click', () => execute(async () => showRelocation(await request('project.relocate_preview',
      { old_workspace: result.plan.oldWorkspace, source_ids: [...selected] }))));
    previewBox.append(refresh); confirmation.textContent = '确认重新关联'; confirmation.hidden = !result.changes.length;
    previewBox.scrollIntoView({ block: 'nearest' });
  }

  // //// 接收搬移前的位置并预览当前项目的配置重连 [@x380kkm 2026-09-08] ////
  function renderRelocation() {
    const section = textElement('section', '', 'settings-section'); section.append(textElement('h3', '项目位置'));
    section.append(textElement('p', '项目已移动或改名时, 用原位置重新关联私人覆盖与宿主备份. 原记录继续保留.', 'card-hint'));
    const location = document.createElement('input'); location.type = 'text'; location.placeholder = '搬移前的绝对路径';
    location.setAttribute('aria-label', '项目原位置'); location.disabled = window.manager.readOnly;
    location.addEventListener('input', () => { preview = null; confirmation.hidden = true; previewBox.replaceChildren(); });
    const button = textElement('button', '预览重新关联'); button.disabled = window.manager.readOnly;
    button.addEventListener('click', () => execute(async () => showRelocation(await request('project.relocate_preview', { old_workspace: location.value.trim() }))));
    section.append(location, button); body.append(section);
  }

  // //// 显示控制状态与现有存档 [@x380kkm 2026-09-07] ////
  function render() {
    body.replaceChildren();
    const control = textElement('section', '', 'settings-section');
    control.append(textElement('h3', scope === 'user' ? '用户级接管' : '当前项目接管'), textElement('p', current.targetRoot, 'card-hint'));
    const label = textElement('label', '', 'host-toggle');
    const enabled = document.createElement('input'); enabled.type = 'checkbox'; enabled.checked = current.enabled; enabled.disabled = window.manager.readOnly;
    enabled.setAttribute('aria-label', '开启配置接管');
    enabled.addEventListener('change', () => execute(async () => {
      const result = await request('host.set_enabled', { enabled: enabled.checked, scope, baseline: current.baseline });
      await load(); await onSaved();
      feedback.textContent = result.hostSync.message;
      if (result.hostSync.diagnostics?.length) showPreview(result.hostSync);
      setStatus(feedback.textContent);
    }));
    label.append(enabled, textElement('span', '开启配置接管')); control.append(label);
    control.append(textElement('p', current.enabled
      ? `接管开启 / ${current.lastApplied ? '存在应用记录' : '尚未应用'}. ${current.initialized ? '原始配置恢复点单独保留' : '首次应用前保存原配置'}, 保存受管设置后应用文件变化.`
      : '接管已关闭. 文件保持不变, 宿主继续使用当前设置. 恢复首次使用前的状态请另行选择恢复原配置.', 'card-hint'));
    control.append(textElement('p', '宿主直接读取配置文件, 管理器退出后仍可使用. 管理器只负责编辑, 组合和写入.', 'card-hint'));
    if (current.recoveryRequired) control.append(textElement('p', '存在未完成的文件写入, 请先在下方选择对应备份恢复.', 'diagnostic'));
    const inspect = textElement('button', '预览待应用设置'); inspect.disabled = window.manager.readOnly || !current.enabled || current.recoveryRequired;
    inspect.addEventListener('click', () => execute(async () => showPreview(await request('host.preview', { scope })))); control.append(inspect);
    body.append(control);
    const backups = textElement('section', '', 'settings-section'); backups.append(textElement('h3', '配置备份'));
    backups.append(textElement('p', current.backupRoot, 'card-hint'));
    if (!current.initialized) backups.append(textElement('p', '首次应用此范围的配置时保存原文, 后续应用保持这份恢复点.', 'card-hint'));
    for (const backup of current.backups) {
      const row = textElement('div', '', 'backup-row');
      row.append(textElement('div', `${backup.label || '配置备份'}\n${new Date(backup.createdAt).toLocaleString('zh-CN')} / ${backup.files.join(', ')}`));
      const restore = textElement('button', backup.id === current.initialBackupId ? '恢复原配置' : '预览恢复'); restore.disabled = window.manager.readOnly || backup.restorable === false;
      if (backup.restorable === false) row.append(textElement('p', backup.restoreError || '此备份的文件归属需要确认.', 'diagnostic'));
      restore.addEventListener('click', () => execute(async () => showPreview(await request('host.preview_restore', { id: backup.id, scope }))));
      row.append(restore); backups.append(row);
    }
    body.append(backups);
    if (scope !== 'user') renderRelocation();
    const hooks = textElement('section', '', 'settings-section'); hooks.append(textElement('h3', 'Hooks 的宿主执行'));
    hooks.append(textElement('p', 'Hooks 按模块与独立内容的使用设置写入宿主文件. 运行与信任由宿主控制, 可按需要开启或关闭.', 'card-hint')); body.append(hooks);
    const source = textElement('section', '', 'settings-section'); source.append(textElement('h3', '配置源码'));
    source.append(textElement('p', '查看完整 JSON, 排查引用或与外部 Agent 协作编辑.', 'card-hint'));
    const open = textElement('button', '打开配置源码'); open.disabled = window.manager.readOnly;
    open.addEventListener('click', () => { dialog.close(); onSource(scope); }); source.append(open); body.append(source);
    previewBox = textElement('section', '', 'settings-section'); body.append(previewBox);
    confirmation.hidden = true;
  }

  // //// 打开当前范围的独立设置面板 [@x380kkm 2026-09-07] ////
  async function open(selectedScope = 'user') {
    scope = selectedScope; dialog.replaceChildren();
    const heading = textElement('div', '', 'dialog-heading');
    const close = textElement('button', '关闭'); close.addEventListener('click', () => dialog.close());
    heading.append(textElement('h2', '配置接管与备份'), close);
    body = textElement('div', '', 'host-settings-body'); feedback = textElement('p', '', 'settings-feedback'); feedback.setAttribute('aria-live', 'polite');
    const actions = textElement('div', '', 'dialog-actions'); confirmation = textElement('button', '应用到文件', 'primary');
    confirmation.addEventListener('click', () => execute(async () => {
      if (preview?.relocationPlan) {
        const result = await request('project.relocate_apply', { plan: preview.relocationPlan });
        await load(); await onSaved();
        feedback.textContent = `项目配置已重新关联, 宿主文件保持不变. 请分别预览应用: ${result.hostApplyRequired.join(', ')}.`;
        return;
      }
      if (!preview?.planId) return;
      const restoring = Boolean(preview.backupId);
      await request('host.apply', { plan_id: preview.planId }); await load(); await onSaved();
      feedback.textContent = restoring ? '所选备份已恢复, 接管已关闭. 恢复前的文件另有保护副本.' : '配置文件已应用. 首次使用前的恢复点保持不变.';
    })); actions.append(confirmation); dialog.append(heading, body, feedback, actions);
    await load(); dialog.showModal();
  }
  return { open };
}
