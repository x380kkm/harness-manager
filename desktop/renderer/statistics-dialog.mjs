// audience: internal
// # statistics-dialog
// 统计展示 Manager 完整读取, 时间窗口采用服务返回的 UTC 日历范围.

import { element, request, textElement, renderDiagnostics } from './view-utils.mjs';
import { createToolIcon } from './tool-icon.mjs';

// //// 展示一段时间内的完整 Skill 读取记录 [@x380kkm 2026-09-07] ////
export function createStatisticsDialog({ run, setStatus }) {
  const dialog = element('statistics-dialog');

  // //// 按时间窗口读取统计并显示真实采集边界 [@x380kkm 2026-09-07] ////
  async function load() {
    const result = await request('statistics.reads', { days: Number(element('statistics-days').value) });
    element('statistics-total').textContent = `${result.total} 次完整读取`;
    element('statistics-window').textContent = `${result.window.start.slice(0, 10)} 至 ${result.window.end.slice(0, 10)} / UTC`;
    const list = element('statistics-skills'); list.replaceChildren();
    for (const skill of result.skills) {
      const row = document.createElement('tr');
      const name = document.createElement('td'); name.append(createToolIcon(skill.name, 'skill', 28), textElement('span', skill.name));
      row.append(name, textElement('td', skill.count), textElement('td', new Date(skill.lastRead).toLocaleString('zh-CN', { hour12: false })));
      list.append(row);
    }
    element('statistics-empty').hidden = result.total > 0;
    renderDiagnostics(element('statistics-error'));
    setStatus('统计只包含经 Manager 完成的读取, 面板预览和重复续读已排除.');
  }

  // //// 打开读取统计窗口 [@x380kkm 2026-09-07] ////
  async function open() {
    element('statistics-days').disabled = window.manager.readOnly === true;
    await load(); dialog.showModal();
  }

  element('statistics-close').addEventListener('click', () => dialog.close());
  element('statistics-days').addEventListener('change', () => run(async () => {
    try { await load(); } catch (error) { renderDiagnostics(element('statistics-error'), [{ message: error.message }]); }
  }));
  return { open };
}
