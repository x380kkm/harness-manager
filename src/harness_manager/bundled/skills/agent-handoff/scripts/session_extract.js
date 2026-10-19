// 按 UTC 时间窗口流式读取 Codex 或 Claude JSONL, 提取对话与工具请求摘要.
const fs = require("node:fs");
const readline = require("node:readline");

const [file, from = "", to = "9999", keepArg = "60"] = process.argv.slice(2);
const keep = Number(keepArg);
if (!file || !Number.isInteger(keep) || keep < 1) {
  console.error("用法: node session_extract.js <会话文件> <起始 UTC> <结束 UTC> [正整数保留条数]");
  process.exit(1);
}
const users = [], agents = [], files = {}, commands = [];
let cwd = "";
const textOf = content => (Array.isArray(content)
  ? content.filter(part => part.type === "text" || part.type === "input_text" || part.type === "output_text").map(part => part.text || "").join("\n")
  : String(content || "")).trim();

function noteFile(filePath, kind, at) {
  const entry = files[filePath] || (files[filePath] = { kinds: {}, first: at, last: at });
  entry.kinds[kind] = (entry.kinds[kind] || 0) + 1;
  entry.last = at;
}

function notePatch(body, at) {
  const pattern = /\*\*\* (Add|Update|Delete) File: (.+?)(?=\\n|\r?\n|"|$)|\*\*\* Move to: (.+?)(?=\\n|\r?\n|"|$)/gm;
  for (const match of body.matchAll(pattern)) {
    noteFile((match[2] || match[3]).trim().replace(/\\\\/g, "\\"), match[1] ? match[1].toLowerCase() : "move", at);
  }
}

function readCodex(entry, at) {
  const payload = entry.payload || {};
  if (entry.type === "event_msg" && payload.type === "user_message") users.push({ at, text: String(payload.message || "") });
  if (entry.type === "event_msg" && payload.type === "agent_message") agents.push({ at, text: String(payload.message || "") });
  if (entry.type !== "response_item") return;
  if (payload.type === "message" && payload.role === "user") users.push({ at, text: textOf(payload.content) });
  if (payload.type === "message" && payload.role === "assistant") agents.push({ at, text: textOf(payload.content) });
  if (/_call$/.test(payload.type || "")) {
    const body = typeof payload.input === "string" ? payload.input : String(payload.arguments || "");
    notePatch(body, at);
    for (const match of body.matchAll(/["']?(?:command|cmd)["']?\s*:\s*"((?:[^"\\]|\\.){4,400})"/g)) commands.push(`${at} ${payload.name}: ${match[1].slice(0, 300)}`);
  }
}

function readClaude(entry, at) {
  const content = entry.message && entry.message.content;
  if (entry.type === "user") {
    const text = textOf(content);
    if (text) users.push({ at, text });
    return;
  }
  agents.push({ at, text: textOf(content) });
  for (const part of Array.isArray(content) ? content : []) {
    if (part.type !== "tool_use") continue;
    const input = part.input || {};
    if (input.file_path) noteFile(input.file_path, part.name.toLowerCase(), at);
    if (input.command) commands.push(`${at} ${part.name}: ${String(input.command).slice(0, 300)}`);
  }
}

const input = fs.createReadStream(file, { encoding: "utf8" });
const reader = readline.createInterface({ input, crlfDelay: Infinity });
input.on("error", error => { console.error(error.message); process.exitCode = 1; reader.close(); });
reader.on("line", line => {
  let entry;
  try { entry = JSON.parse(line); } catch { return; }
  if (!entry || typeof entry !== "object") return;
  if (entry.cwd) cwd = entry.cwd;
  if ((entry.type === "session_meta" || entry.type === "turn_context") && entry.payload && entry.payload.cwd) cwd = entry.payload.cwd;
  const timestamp = Date.parse(entry.timestamp);
  if (!Number.isFinite(timestamp)) return;
  const at = new Date(timestamp).toISOString();
  if (at < from || at >= to) return;
  if (entry.payload) readCodex(entry, at);
  else if (entry.message && (entry.type === "user" || entry.type === "assistant")) readClaude(entry, at);
});
reader.on("close", () => {
  if (process.exitCode) return;
  const recent = list => list.filter(item => item.text).slice(-keep).map(item => ({ at: item.at, text: item.text.slice(0, 900) }));
  console.log(JSON.stringify({
    cwd, window: [from, to], timezone: "UTC",
    userMessages: recent(users), agentMessages: recent(agents),
    files, commands: commands.slice(-keep),
    counts: { users: users.length, agents: agents.length, files: Object.keys(files).length, commands: commands.length },
  }, null, 1));
});
