// audience: internal
// # tool-icon
// 用途图标由工具名称和内容类型确定, 卡片与详情共用同一组矢量符号.

const namespace = 'http://www.w3.org/2000/svg';
const symbols = {
  module: ['#e7e6dd', '#2c4239', ['M4 4H13V13H4ZM19 4H28V13H19ZM11 21H21V30H11Z', 'M8 13V17H24V13M16 17V21']],
  hook: ['#e7e3e6', '#795f72', ['M4 4H28V10H4ZM10 10V16H23V24M19 20L23 24L27 20M5 28H13']],
  codegraph: ['#e3edf8', '#38638f', ['M8 9L16 6L24 14L16 24L8 19Z', 'M8 9L16 16L24 14M16 6V16L8 19M16 16V24'], [[8, 9], [16, 6], [24, 14], [16, 24], [8, 19], [16, 16]]],
  workflow: ['#eee7f6', '#72558e', ['M16 4L22 10L16 16L10 10Z', 'M16 16V20M8 20H24M8 20V24M24 20V24', 'M4 24H12V29H4ZM20 24H28V29H20Z']],
  search: ['#e2eeec', '#3f746e', ['M18 18L27 27', 'M10 9L7 12L10 15M16 9L19 12L16 15'], [[13, 12, 9]]],
  writing: ['#f1e9dc', '#8c6838', ['M6 25L8 17L22 3L28 9L14 23Z', 'M8 17L14 23M19 6L25 12M6 29H27']],
  performance: ['#e5eddf', '#5d7b43', ['M5 5V27H28', 'M8 20L13 11L18 17L23 7L28 12']],
  slides: ['#f4e5dd', '#966049', ['M4 5H28V23H4ZM10 29L16 23L22 29M10 17V13M16 17V9M22 17V11']],
  tiles: ['#ede8e1', '#786448', ['M4 4H28V28H4ZM4 12H28M12 4V12M20 4V12M16 12V20M4 20H28M10 20V28M23 20V28']],
  adapter: ['#e3edf0', '#487887', ['M10 4V11M22 4V11M7 11H25V15L21 21H11L7 15ZM16 21V28']],
  image: ['#eee5eb', '#895e7d', ['M4 5H28V27H4ZM4 22L11 15L17 20L22 13L28 20'], [[10, 10, 2]]],
  document: ['#e8ebf5', '#586d96', ['M7 3H20L26 9V29H7ZM20 3V10H26M11 15H22M11 20H22M11 25H19']],
  terminal: ['#e7ecef', '#536f7e', ['M3 5H29V27H3ZM8 12L13 16L8 20M17 21H24']],
  browser: ['#e5eeed', '#42716b', ['M3 16H29M16 3C7 10 7 22 16 29C25 22 25 10 16 3'], [[16, 16, 13]]],
  cursor: ['#e6eaf2', '#536c91', ['M7 3L28 18L18 20L14 29Z']],
  settings: ['#e8ebee', '#586b7e', ['M5 8H27M5 16H27M5 24H27', 'M10 5V11M22 13V19M13 21V27']],
  package: ['#e9e9ef', '#676486', ['M16 3L28 9V23L16 29L4 23V9ZM4 9L16 15L28 9M16 15V29M10 6L22 12']],
};
const routes = [
  [/codegraph/i, 'codegraph'], [/archify|workflow|flowchart|流程图/i, 'workflow'], [/clean-tools|cleanread|cleanscan|audit/i, 'search'],
  [/writ|写作|word|文风/i, 'writing'], [/matrix|performance|profiler|性能/i, 'performance'], [/ppt|present|slide/i, 'slides'],
  [/trim|sheet-design|material/i, 'tiles'], [/adapter|codexpro|connector|mcp/i, 'adapter'], [/image|theme|photo|design/i, 'image'],
  [/pdf|document|agents|说明|规则/i, 'document'], [/shell|terminal|powershell/i, 'terminal'], [/browser|chrome|web|sites/i, 'browser'],
  [/spreadsheet|excel/i, 'tiles'], [/computer-use/i, 'cursor'], [/manager|config|设置|配置/i, 'settings'],
];

// //// 为工具选择与用途对应的符号 [@x380kkm 2026-09-07] ////
export function toolIconName(name, kind = '') {
  if (kind === 'module' || kind === 'hook') return kind;
  const selected = routes.find(([pattern]) => pattern.test(name));
  if (selected) return selected[1];
  return ({ instruction: 'document', rule: 'document', section: 'document', config: 'settings', connection: 'adapter', mcp: 'adapter' })[kind] || 'package';
}

// //// 用独立 SVG 元素绘制工具图标 [@x380kkm 2026-09-07] ////
export function createToolIcon(name, kind = '', size = 44) {
  const iconName = toolIconName(name, kind);
  const [background, foreground, paths, circles = []] = symbols[iconName];
  const svg = document.createElementNS(namespace, 'svg');
  for (const [key, value] of Object.entries({ viewBox: '0 0 44 44', width: size, height: size, class: 'tool-icon', 'aria-hidden': 'true', 'data-icon-name': iconName })) svg.setAttribute(key, String(value));
  const backdrop = document.createElementNS(namespace, 'rect');
  for (const [key, value] of Object.entries({ width: 44, height: 44, rx: 10, fill: background })) backdrop.setAttribute(key, String(value));
  const drawing = document.createElementNS(namespace, 'g');
  for (const [key, value] of Object.entries({ transform: 'translate(6 6)', fill: 'none', stroke: foreground, 'stroke-width': 1.8, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' })) drawing.setAttribute(key, String(value));
  for (const [cx, cy, r = 2] of circles) {
    const circle = document.createElementNS(namespace, 'circle');
    for (const [key, value] of Object.entries({ cx, cy, r, fill: background })) circle.setAttribute(key, String(value));
    drawing.append(circle);
  }
  for (const d of paths) { const path = document.createElementNS(namespace, 'path'); path.setAttribute('d', d); drawing.append(path); }
  svg.append(backdrop, drawing);
  return svg;
}
