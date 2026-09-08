// audience: internal
// # catalog-view
// 列表与分类只呈现管理核心返回的登记内容.

import { displayValue, textElement } from './view-utils.mjs';

// //// 按种类和搜索文本筛选声明 [@x380kkm 2026-09-06] ////
export function filterDocuments(documents, kind, query) {
  const search = query.trim().toLocaleLowerCase();
  return documents.filter((item) => {
    if (kind && item.kind !== kind) return false;
    return !search || [item.id, item.name, item.summary, item.kind].some((value) => String(value || '').toLocaleLowerCase().includes(search));
  });
}

// //// 显示实际登记内容的分类 [@x380kkm 2026-09-06] ////
export function renderKinds(container, documents, selected, onSelect) {
  const counts = new Map();
  for (const item of documents) counts.set(item.kind, (counts.get(item.kind) || 0) + 1);
  const kinds = [['', documents.length], ...[...counts.entries()].sort(([first], [second]) => first.localeCompare(second))];
  container.replaceChildren();
  for (const [kind, count] of kinds) {
    const button = textElement('button', kind || '全部声明', 'kind-item');
    if (kind === selected) button.setAttribute('aria-current', 'page');
    button.append(textElement('span', count, 'kind-count'));
    button.addEventListener('click', () => onSelect(kind));
    container.append(button);
  }
}

// //// 生成可通过键盘选择的声明行 [@x380kkm 2026-09-06] ////
function documentRow(item, selected, onSelect) {
  const row = document.createElement('tr');
  row.tabIndex = 0;
  row.classList.toggle('selected', item.id === selected);
  row.setAttribute('aria-selected', String(item.id === selected));
  const name = document.createElement('td');
  name.append(textElement('span', item.name || item.id, 'document-name'));
  name.append(textElement('span', item.summary || item.id, 'document-summary'));
  const kind = document.createElement('td');
  kind.append(textElement('span', item.kind, 'kind-badge'));
  row.append(name, kind, textElement('td', displayValue(item.version)), textElement('td', displayValue(item.target)));
  row.addEventListener('click', () => onSelect(item.id));
  row.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    onSelect(item.id);
  });
  return row;
}

// //// 显示筛选后的声明列表 [@x380kkm 2026-09-06] ////
export function renderDocuments(container, documents, selected, onSelect) {
  container.replaceChildren(...documents.map((item) => documentRow(item, selected, onSelect)));
}
