// audience: internal
// # markdown-view
// 正文通过关闭 HTML 与外部资源的 Markdown 渲染器显示, 来源文本保持原样.

import MarkdownIt from 'markdown-it';
import { textElement } from './view-utils.mjs';

const renderer = new MarkdownIt({ html: false }).disable(['image', 'link']);

// //// 呈现规则正文的段落, 列表, 代码和表格 [@x380kkm 2026-09-07] ////
export function markdownView(text, className = '') {
  const element = textElement('div', '', `markdown-content ${className}`.trim());
  element.innerHTML = renderer.render(text || '');
  return element;
}
