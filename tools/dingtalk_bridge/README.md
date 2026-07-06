# 钉钉 ↔ Claude Code 桥

在钉钉里 @机器人 发一句话 → Claude Code(全权限, 工作目录=本仓库)干活 → 回答发回钉钉。
等价于「用钉钉当 Claude Code 的终端」。

```
你 →[钉钉]→ 机器人 →(Stream长连接)→ bridge.py → claude -p(全权限,操作本仓库)
你 ←[钉钉]←──────────────── 回答 ←──────────────┘
```

## 1. 建一个「企业内部机器人」（拿 AppKey/AppSecret）

1. 登录 [钉钉开放平台](https://open-dev.dingtalk.com/) → 「应用开发」→ 创建**企业内部应用**。
2. 进应用 → 「机器人」能力 → 添加机器人。
3. **消息接收模式选「Stream 模式」**（免公网 IP/域名，本机就能收）。
4. 在应用「凭证与基础信息」里拿到 **AppKey** 和 **AppSecret**。
5. 发布/上线应用，把机器人加进一个群（或单聊）。

## 2. 拿到你自己的 staffId（白名单要用）

- 钉钉管理后台 → 通讯录 → 找到自己 → 看 userid；
- 或先不填白名单启动，随便发条消息，日志/未授权回复里会打印你的 `staffId`，再填进去。

> ⚠️ **务必设白名单**。这个机器人执行的是**全权限** Claude（能读写文件、跑命令）。
> 不设白名单 = 任何能给机器人发消息的人都能在你电脑上执行任意操作。

## 3. 配置 .env（仓库根目录）

把下面几行加到 `/Users/admin/formal/FinParseAI/.env`：

```ini
DINGTALK_APP_KEY=你的AppKey
DINGTALK_APP_SECRET=你的AppSecret
DINGTALK_ALLOWED_STAFF=你的staffId          # 多个用逗号隔开
# 可选：
# CLAUDE_WORKDIR=/Users/admin/formal/FinParseAI
# CLAUDE_MODEL=claude-opus-4-8
# CLAUDE_TIMEOUT=1800
# CLAUDE_BIN=/Users/admin/.local/bin/claude
```

`.env` 已被 gitignore，凭证不会进仓库。

## 4. 跑起来

```bash
cd /Users/admin/formal/FinParseAI
python3 tools/dingtalk_bridge/bridge.py
```

看到「已连接钉钉 Stream，等待消息…」就成了。去钉钉 @机器人 发消息：

- 「看看 src/eval/canonical.py 是干嘛的」
- 「跑一下测试，把失败的贴给我」
- 「给 triage_queue.py 加个类型注解」
- `/reset` 清空上下文重开；`/help` 看说明。

## 依赖

`dingtalk-stream`（已装）。缺的话：`pip3 install dingtalk-stream`。
`claude` CLI 用**你当前登录的账号**跑（bridge 以你的身份启动），无需额外配 API key。

### reclaude 用户注意（本机就是）

你的 `claude` 走 **reclaude 本地代理**（`~/.reclaude/`，这样才有 Opus 4.8 1M）。
reclaude 靠交互式 shell 由 daemon 注入 `HTTP(S)_PROXY` + `NODE_EXTRA_CA_CERTS`；
而 bridge 用 `subprocess` 直接调 claude 会绕过这些注入 → claude 直连 Anthropic 却带着
reclaude 的 token → **401 Invalid bearer token**。

`bridge.py` 的 `_child_env()` 已自动处理：从 `~/.reclaude/state.json` 读 daemon 当前端口，
给子进程补上代理与 CA（端口每次可能变，动态读，无需手配）。前提是 reclaude daemon 在跑
（你平时能正常用 `claude` 就说明在跑）。

## 常驻后台（可选）

前台调通后想让它一直在，可挂 launchd（开机自启）或简单 `nohup`：

```bash
nohup python3 tools/dingtalk_bridge/bridge.py > /tmp/ding-bridge.log 2>&1 &
tail -f /tmp/ding-bridge.log
```

## 安全清单

- ✅ 只填**你自己**的 staffId 进白名单。
- ✅ 机器人所在的群别拉外人。
- ⚠️ 全权限意味着一句「删掉 xxx」它真会删。重要操作先在钉钉里说清楚、看它回的计划再让它动手。
- ⚠️ 想更保守：把 `bridge.py` 里 `--dangerously-skip-permissions` 换成
  `--allowedTools Read Grep Glob Bash`（按需给），或 `--permission-mode plan` 先只出方案。
