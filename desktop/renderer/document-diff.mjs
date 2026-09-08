// audience: internal
// # document-diff
// 字段高亮使用管理计划的 JSON Pointer. 数组变化沿用核心提供的字段范围.

import { element, textElement } from './view-utils.mjs';

// //// 生成 JSON Pointer 的字段片段 [@x380kkm 2026-09-06] ////
function pointerPart(key) {
  return String(key).replaceAll('~', '~0').replaceAll('/', '~1');
}

// //// 将 JSON 内容展开成带字段路径的文本行 [@x380kkm 2026-09-06] ////
function documentLines(document) {
  if (document === null) return [];
  const rows = [];
  // //// 递归展开字段并保留格式化行的路径 [@x380kkm 2026-09-06] ////
  function append(value, path, depth, prefix = '', suffix = '') {
    const indent = '  '.repeat(depth);
    const composite = value !== null && typeof value === 'object';
    const entries = composite ? Object.entries(value) : [];
    if (!entries.length) {
      rows.push({ text: `${indent}${prefix}${JSON.stringify(value)}${suffix}`, path });
      return;
    }
    const array = Array.isArray(value);
    rows.push({ text: `${indent}${prefix}${array ? '[' : '{'}`, path });
    entries.forEach(([key, child], index) => {
      append(child, `${path}/${pointerPart(key)}`, depth + 1, array ? '' : `${JSON.stringify(key)}: `, index < entries.length - 1 ? ',' : '');
    });
    rows.push({ text: `${indent}${array ? ']' : '}'}${suffix}`, path });
  }
  append(document, '', 0);
  return rows;
}

// //// 标记计划实际改变的字段行 [@x380kkm 2026-09-06] ////
function markRows(document, paths) {
  return documentLines(document).map((row) => ({
    ...row,
    changed: paths.some((path) => row.path === path || row.path.startsWith(`${path}/`)),
  }));
}

// //// 根据核心计划取得登记前后的差异行 [@x380kkm 2026-09-06] ////
export function planDifferences(plan) {
  return {
    before: markRows(plan.before, plan.changes.filter((change) => change.operation !== 'add').map((change) => change.path)),
    after: markRows(plan.after, plan.changes.filter((change) => change.operation !== 'remove').map((change) => change.path)),
  };
}

// //// 显示核心计划指定的字段差异 [@x380kkm 2026-09-06] ////
export function renderPlanDifference(plan) {
  const rows = planDifferences(plan);
  for (const [id, values, kind] of [['before-content', rows.before, 'removed'], ['after-content', rows.after, 'added']]) {
    element(id).replaceChildren(...values.map((row) => {
      const line = textElement('span', row.text || ' ', `diff-line${row.changed ? ` ${kind}` : ''}`);
      line.setAttribute('data-json-path', row.path);
      return line;
    }));
  }
  element('changed-fields').textContent = plan.changes.length
    ? plan.changes.map((change) => change.path).join(', ')
    : '内容与登记一致.';
}
