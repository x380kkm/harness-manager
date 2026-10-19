// # session-memory-tests
// 临时宿主目录和转写样本验证请求记录, 答复注入和出错时的状态保留.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";

const script = fileURLToPath(new URL("../src/harness_manager/bundled/tools/session-memory/session-memory.mjs", import.meta.url));

// //// 在隔离配置目录调用实际 Hook 入口 [@x380kkm 2026-10-19] ////
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "session-memory-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const env = { ...process.env, CODEX_HOME: path.join(root, "codex"), CLAUDE_CONFIG_DIR: path.join(root, "claude"),
    CODEX_HOOK_NESTED: "", CLAUDE_HOOK_NESTED: "" };
  return { root, env, run(host, event, input, extraEnv = {}) {
    const result = spawnSync(process.execPath, [script, host, event], {
      input: typeof input === "string" ? input : JSON.stringify(input), encoding: "utf8", env: { ...env, ...extraEnv },
    });
    assert.equal(result.status, 0, result.stderr);
    return JSON.parse(result.stdout);
  } };
}

// //// 两个宿主从实际事件结构提取正文并在下次请求注入 [@x380kkm 2026-10-19] ////
test("请求记录与转写答复按宿主和会话隔离", (t) => {
  const f = fixture(t);
  for (const host of ["codex", "claude"]) {
    const input = { session_id: "../../shared-session", cwd: f.root, prompt: "保存用户请求." };
    f.run(host, "record", input);
    const transcript = path.join(f.root, `${host}.jsonl`);
    const records = host === "codex" ? [
      { type: "session_meta", payload: { id: input.session_id, thread_source: "user", source: "cli" } },
      { type: "event_msg", payload: { type: "user_message", message: "请求." } },
      { type: "response_item", payload: { type: "message", role: "assistant", phase: "commentary", content: [{ type: "output_text", text: "处理中." }] } },
      { type: "response_item", payload: { type: "message", role: "assistant", phase: "final_answer", content: [{ type: "output_text", text: `${host} 已输出结论.` }] } },
    ] : [
      { type: "user", message: { role: "user", content: "请求." } },
      { type: "assistant", message: { role: "assistant", content: [{ type: "thinking", thinking: "内部推理." }] } },
      { type: "assistant", message: { role: "assistant", content: [{ type: "text", text: `${host} 已输出结论.` }] } },
      { type: "assistant", isSidechain: true, message: { role: "assistant", content: [{ type: "text", text: "子代理结论." }] } },
    ];
    fs.writeFileSync(transcript, records.map((record) => JSON.stringify(record)).join("\n"), "utf8");
    assert.deepEqual(f.run(host, "recap", { ...input, transcript_path: transcript }), {});
    const context = f.run(host, "record", { ...input, prompt: "继续." }).hookSpecificOutput.additionalContext;
    assert.match(context, new RegExp(`${host} 已输出结论`));
    assert.match(context, /保存用户请求/);
    assert.match(context, /未经独立审计/);
    assert.doesNotMatch(context, /内部推理|子代理结论|处理中/);
    const other = f.run(host, "record", { ...input, session_id: "other", prompt: "另一会话." }).hookSpecificOutput.additionalContext;
    assert.doesNotMatch(other, /保存用户请求|已输出结论/);
    const files = fs.readdirSync(path.join(f.env[host === "codex" ? "CODEX_HOME" : "CLAUDE_CONFIG_DIR"], "session-memory"));
    assert.equal(files.length, 2);
    assert.ok(files.every((file) => /^[a-f0-9]{64}\.json$/.test(file)));
  }
  assert.equal(fs.existsSync(path.join(f.root, "archive")), false);
});

// //// 稳定的停止事件字段优先且异常输入保留已存答复 [@x380kkm 2026-10-19] ////
test("停止字段与失败保护保持回顾可用", (t) => {
  const f = fixture(t);
  const input = { session_id: "session", cwd: f.root, thread_source: "user" };
  f.run("codex", "record", { ...input, prompt: "请求." });
  f.run("codex", "recap", { ...input, last_assistant_message: "实际答复.", transcript_path: "missing" });
  for (const extra of [
    { transcript_path: "missing" }, { last_assistant_message: "覆写.", stop_hook_active: true },
    { last_assistant_message: "覆写.", agent_id: "child" }, { last_assistant_message: "覆写.", subagent_id: "child" },
    { last_assistant_message: "覆写.", parent_thread_id: "parent" },
    { last_assistant_message: "覆写.", thread_source: "subagent" },
    { last_assistant_message: "覆写.", source: { subagent: { parent_thread_id: "parent" } } },
  ]) assert.deepEqual(f.run("codex", "recap", { ...input, ...extra }), {});
  f.run("codex", "recap", { ...input, last_assistant_message: "覆写." }, { CODEX_HOOK_NESTED: "1" });
  assert.deepEqual(f.run("codex", "recap", "malformed"), {});
  const broken = path.join(f.root, "broken.jsonl");
  fs.writeFileSync(broken, "malformed\n", "utf8");
  f.run("codex", "recap", { ...input, transcript_path: broken });
  const output = f.run("codex", "record", { ...input, prompt: "继续." }).hookSpecificOutput.additionalContext;
  assert.match(output, /实际答复/);
  assert.doesNotMatch(output, /覆写/);
  assert.deepEqual(f.run("codex", "recap", { last_assistant_message: "无会话." }), {});
});

// //// 元数据中的子代理或未知来源保留主线程已有回顾 [@x380kkm 2026-10-19] ////
test("停止事件先确认主线程来源", (t) => {
  const f = fixture(t);
  const input = { session_id: "session", cwd: f.root };
  f.run("codex", "recap", { ...input, thread_source: "user", last_assistant_message: "主线程答复." });
  const transcript = path.join(f.root, "session.jsonl");
  for (const meta of [
    { thread_source: "subagent" },
    { thread_source: "user", parent_thread_id: "parent" },
    { thread_source: "user", source: { subagent: "child" } },
    { source: "unknown" },
  ]) {
    fs.writeFileSync(transcript, JSON.stringify({ type: "session_meta", payload: meta }) + "\n", "utf8");
    f.run("codex", "recap", { ...input, transcript_path: transcript, last_assistant_message: "覆写." });
  }
  f.run("codex", "recap", { ...input, last_assistant_message: "覆写." });
  const context = f.run("codex", "record", { ...input, prompt: "继续." }).hookSpecificOutput.additionalContext;
  assert.match(context, /主线程答复/);
  assert.doesNotMatch(context, /覆写/);
});
