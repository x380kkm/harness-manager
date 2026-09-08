// audience: internal
// # declaration-graph
// 节点和关系使用目录提供的稳定 ID. 画布位置属于阅读视图.

const namespace = 'http://www.w3.org/2000/svg';
const nodeWidth = 212;
const nodeHeight = 74;

// //// 解析图节点对应的登记声明 [@x380kkm 2026-09-06] ////
export function registeredDocumentId(node) {
  return node.documentId === undefined ? node.id : node.documentId;
}

// //// 保留选定声明的节点及关联的未登记引用 [@x380kkm 2026-09-06] ////
export function graphForDocuments(graph, documentIds, filtered) {
  const visible = new Set(graph.nodes.filter((node) => documentIds.has(registeredDocumentId(node))).map((node) => node.id));
  const neighbors = new Set();
  for (const edge of graph.edges) {
    if (visible.has(edge.from)) neighbors.add(edge.to);
    if (visible.has(edge.to)) neighbors.add(edge.from);
  }
  for (const node of graph.nodes) {
    if (registeredDocumentId(node) === null && (!filtered || neighbors.has(node.id))) visible.add(node.id);
  }
  return {
    nodes: graph.nodes.filter((node) => visible.has(node.id)),
    edges: graph.edges.filter((edge) => visible.has(edge.from) && visible.has(edge.to)),
  };
}

// //// 创建 SVG 元素并设置属性 [@x380kkm 2026-09-06] ////
function svgElement(tag, attributes = {}, text) {
  const node = document.createElementNS(namespace, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  if (text != null) node.textContent = String(text);
  return node;
}

// //// 缩短节点文字并保留完整提示 [@x380kkm 2026-09-06] ////
function shorten(text, limit) {
  const value = String(text || '');
  return value.length > limit ? `${value.slice(0, limit - 3)}...` : value;
}

// //// 根据关系层次排列目录节点 [@x380kkm 2026-09-06] ////
export function layoutNodes(nodes, edges) {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const incoming = new Map(nodes.map((node) => [node.id, 0]));
  const outgoing = new Map(nodes.map((node) => [node.id, []]));
  const depth = new Map(nodes.map((node) => [node.id, 0]));
  for (const edge of edges) {
    if (!byId.has(edge.from) || !byId.has(edge.to) || edge.from === edge.to) continue;
    incoming.set(edge.to, incoming.get(edge.to) + 1);
    outgoing.get(edge.from).push(edge.to);
  }
  const queue = nodes.filter((node) => incoming.get(node.id) === 0).map((node) => node.id);
  for (let index = 0; index < queue.length; index += 1) {
    const id = queue[index];
    for (const target of outgoing.get(id)) {
      depth.set(target, Math.max(depth.get(target), depth.get(id) + 1));
      incoming.set(target, incoming.get(target) - 1);
      if (incoming.get(target) === 0) queue.push(target);
    }
  }
  const rows = new Map();
  const positions = new Map();
  for (const node of nodes) {
    const column = Math.min(depth.get(node.id), 7);
    const row = rows.get(column) || 0;
    positions.set(node.id, { x: 50 + column * 288, y: 44 + row * 124 });
    rows.set(column, row + 1);
  }
  return positions;
}

// //// 生成指向目标节点的关系线 [@x380kkm 2026-09-06] ////
function drawEdge(edge, positions, markerId) {
  const from = positions.get(edge.from);
  const to = positions.get(edge.to);
  if (!from || !to) return null;
  const sameColumn = from.x === to.x;
  const start = { x: from.x + nodeWidth, y: from.y + nodeHeight / 2 };
  const end = { x: sameColumn ? to.x + nodeWidth : to.x, y: to.y + nodeHeight / 2 };
  const bend = sameColumn ? start.x + 50 : (start.x + end.x) / 2;
  const path = edge.from === edge.to
    ? `M ${start.x} ${start.y - 10} C ${bend + 15} ${start.y - 45}, ${bend + 15} ${start.y + 45}, ${end.x} ${end.y + 10}`
    : `M ${start.x} ${start.y} C ${bend} ${start.y}, ${bend} ${end.y}, ${end.x} ${end.y}`;
  const group = svgElement('g');
  group.append(svgElement('path', { d: path, class: 'graph-edge', 'marker-end': `url(#${markerId})` }));
  if (edge.label) group.append(svgElement('text', { x: bend, y: (start.y + end.y) / 2 - 7, 'text-anchor': 'middle', class: 'graph-edge-label' }, shorten(edge.label, 24)));
  group.append(svgElement('title', {}, `${edge.from} -> ${edge.to}${edge.label ? `: ${edge.label}` : ''}`));
  return group;
}

// //// 创建有键盘入口的声明节点 [@x380kkm 2026-09-06] ////
function drawNode(node, position, onSelect) {
  const group = svgElement('g', { class: 'graph-node', transform: `translate(${position.x} ${position.y})`, 'data-node-id': node.id, tabindex: 0, role: 'button', 'aria-label': node.label || node.id });
  group.append(svgElement('rect', { width: nodeWidth, height: nodeHeight, rx: 7 }));
  group.append(svgElement('text', { x: 14, y: 25 }, shorten(node.label || node.id, 19)));
  group.append(svgElement('text', { x: 14, y: 48, class: 'node-kind' }, shorten(node.kind, 30)));
  group.append(svgElement('title', {}, `${node.label || node.id}\n${node.id}`));
  group.addEventListener('click', () => onSelect(node.id));
  group.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    onSelect(node.id);
  });
  return group;
}

// //// 维护关系图的阅读视口 [@x380kkm 2026-09-06] ////
export function createGraph(svg, onSelect, layout = layoutNodes) {
  const markerId = `${svg.id || 'graph'}-arrow`;
  let bounds = { x: 0, y: 0, width: 500, height: 400 };
  let viewport = { ...bounds };
  let positions = new Map();
  let dragging = null;
  let initialized = false;

  // //// 更新画布可见范围 [@x380kkm 2026-09-06] ////
  function applyViewport() {
    svg.setAttribute('viewBox', `${viewport.x} ${viewport.y} ${viewport.width} ${viewport.height}`);
  }

  // //// 显示节点与有效关系 [@x380kkm 2026-09-06] ////
  function render(nodes, edges, selected) {
    positions = layout(nodes, edges);
    const defs = svgElement('defs');
    const marker = svgElement('marker', { id: markerId, viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: 'auto-start-reverse' });
    marker.append(svgElement('path', { d: 'M 0 0 L 10 5 L 0 10 z', class: 'graph-arrow' }));
    defs.append(marker);
    svg.replaceChildren(defs);
    for (const edge of edges) {
      const group = drawEdge(edge, positions, markerId);
      if (group) svg.append(group);
    }
    for (const node of nodes) svg.append(drawNode(node, positions.get(node.id), onSelect));
    bounds = { x: 0, y: 0, width: Math.max(440, ...[...positions.values()].map((point) => point.x + nodeWidth + 100)), height: Math.max(320, ...[...positions.values()].map((point) => point.y + nodeHeight + 100)) };
    if (!initialized && nodes.length) {
      viewport = { ...bounds };
      initialized = true;
    }
    applyViewport();
    select(selected);
  }

  // //// 清空项目图形及其阅读位置 [@x380kkm 2026-09-06] ////
  function clear() {
    positions = new Map();
    bounds = { x: 0, y: 0, width: 500, height: 400 };
    viewport = { ...bounds };
    initialized = false;
    svg.replaceChildren();
    applyViewport();
  }

  // //// 标记当前选择的节点 [@x380kkm 2026-09-06] ////
  function select(id) {
    for (const node of svg.querySelectorAll('[data-node-id]')) {
      const selected = node.getAttribute('data-node-id') === id;
      node.classList.toggle('selected', selected);
      node.setAttribute('aria-pressed', String(selected));
    }
  }

  // //// 将全部节点置于可见范围 [@x380kkm 2026-09-06] ////
  function fit() {
    viewport = { ...bounds };
    applyViewport();
  }

  // //// 以视口中心缩放关系图 [@x380kkm 2026-09-06] ////
  function zoom(factor) {
    const width = Math.max(180, Math.min(bounds.width * 3, viewport.width * factor));
    const height = viewport.height * width / viewport.width;
    viewport = { x: viewport.x + (viewport.width - width) / 2, y: viewport.y + (viewport.height - height) / 2, width, height };
    applyViewport();
  }

  // //// 将选中声明定位到画布中心 [@x380kkm 2026-09-06] ////
  function reveal(id) {
    const point = positions.get(id);
    if (!point) return;
    viewport = { ...viewport, x: point.x + nodeWidth / 2 - viewport.width / 2, y: point.y + nodeHeight / 2 - viewport.height / 2 };
    applyViewport();
  }

  svg.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || event.target.closest('[data-node-id]')) return;
    dragging = { x: event.clientX, y: event.clientY, viewport: { ...viewport } };
    svg.setPointerCapture(event.pointerId);
    svg.classList.add('panning');
  });
  svg.addEventListener('pointermove', (event) => {
    if (!dragging) return;
    const scale = Math.max(viewport.width / svg.clientWidth, viewport.height / svg.clientHeight);
    viewport.x = dragging.viewport.x - (event.clientX - dragging.x) * scale;
    viewport.y = dragging.viewport.y - (event.clientY - dragging.y) * scale;
    applyViewport();
  });
  for (const name of ['pointerup', 'pointercancel']) svg.addEventListener(name, () => { dragging = null; svg.classList.remove('panning'); });
  svg.addEventListener('wheel', (event) => {
    if (!event.ctrlKey) return;
    event.preventDefault();
    zoom(event.deltaY > 0 ? 1.15 : 1 / 1.15);
  }, { passive: false });

  return { render, select, clear, fit, zoom, reveal };
}
