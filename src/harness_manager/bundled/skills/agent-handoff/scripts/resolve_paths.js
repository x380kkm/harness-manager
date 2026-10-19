// 只读解析项目工作树, 私人交接目录和宿主会话位置. Git 目录通过 Git 自身查询.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { parseArgs } = require("node:util");

function absolutePath(value, base = process.cwd()) {
  if (value === "~") return os.homedir();
  if (/^~[/\\]/.test(value)) return path.resolve(os.homedir(), value.slice(2));
  return path.resolve(base, value);
}

function git(directory, args, allowNonRepository = false) {
  const result = spawnSync("git", ["-C", directory, ...args], { encoding: "utf8", windowsHide: true });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    if (allowNonRepository && /not a git repository/i.test(result.stderr)) return null;
    throw new Error(result.stderr.trim() || `Git exited with status ${result.status}`);
  }
  return result.stdout;
}

function resolvePaths(options) {
  const requested = absolutePath(options.project || process.cwd());
  if (!fs.statSync(requested).isDirectory()) throw new Error(`项目路径不是目录: ${requested}`);
  const gitRoot = git(requested, ["rev-parse", "--show-toplevel"], true);
  const projectRoot = gitRoot === null ? requested : absolutePath(gitRoot.trim());
  let sharedRoot = projectRoot;
  let gitExcludeFile = null;
  const warnings = [];
  if (gitRoot !== null) {
    const firstWorktree = git(projectRoot, ["worktree", "list", "--porcelain", "-z"]).split("\0\0")[0].split("\0");
    const main = firstWorktree.find(field => field.startsWith("worktree "));
    const mainPath = main && absolutePath(main.slice(9));
    if (mainPath && !firstWorktree.includes("bare") && fs.existsSync(path.join(mainPath, ".git"))) {
      sharedRoot = mainPath;
    } else {
      warnings.push("Git 未提供可确认的主工作树, 使用当前工作树. 跨工作树共享交接时请指定 --handoff-dir.");
    }
    gitExcludeFile = absolutePath(git(projectRoot, ["rev-parse", "--path-format=absolute", "--git-path", "info/exclude"]).trim());
  }
  const codexRoot = absolutePath(options["codex-home"] || process.env.CODEX_HOME || path.join(os.homedir(), ".codex"));
  const claudeRoot = absolutePath(options["claude-home"] || process.env.CLAUDE_CONFIG_DIR || path.join(os.homedir(), ".claude"));
  const codexSessions = path.join(codexRoot, "sessions");
  const claudeProjects = path.join(claudeRoot, "projects");
  return {
    projectRoot, sharedRoot,
    handoffDir: absolutePath(options["handoff-dir"] || "archive", sharedRoot),
    gitExcludeFile, codexSessions, claudeProjects, warnings,
    sessionDirectoriesExist: { codex: fs.existsSync(codexSessions), claude: fs.existsSync(claudeProjects) },
  };
}

try {
  const { values } = parseArgs({ options: {
    project: { type: "string" },
    "handoff-dir": { type: "string" },
    "codex-home": { type: "string" },
    "claude-home": { type: "string" },
  } });
  console.log(JSON.stringify(resolvePaths(values), null, 2));
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
