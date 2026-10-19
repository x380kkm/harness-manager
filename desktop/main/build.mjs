// audience: internal
// # desktop-build
// 页面脚本随桌面应用分发, 同一产物支持本地静态文件入口.

import { build } from 'esbuild';

// //// 生成页面脚本 [@x380kkm 2026-09-07] ////
await build({
  entryPoints: ['renderer/bootstrap.mjs'], bundle: true, outfile: 'renderer/app.js',
});
// //// /生成页面脚本 ////
