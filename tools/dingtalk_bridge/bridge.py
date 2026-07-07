"""
钉钉 ↔ Claude Code 桥
================================================================================
你在钉钉 @机器人 发一句话 → 本服务把它喂给 Claude Code(headless, 全权限, 工作目录=本仓库)
→ Claude 干完活 → 回答通过钉钉发回给你。等价于"用钉钉当 Claude Code 的终端"。

关键设计
--------
· 收消息:钉钉 Stream Mode 长连接(免公网 IP/域名)。dingtalk_stream 框架已把 process()
  丢到线程池执行并**立即 ack**,所以 Claude 跑几分钟也不会触发消息重投。
· 跑 Claude:`claude -p <文本> --output-format json --dangerously-skip-permissions`
  —— 全权限 = 和你终端一样(能读写文件、跑命令)。cwd=仓库根。
· 会话连续:按发信人存 session_id,下一条带 `--resume` 续上下文。发 "/reset" 清空重开。
· 串行执行:全局锁,一次只跑一个 claude —— 并发改同一个仓库会互相踩踏。
· 安全闸:仅 DINGTALK_ALLOWED_STAFF 白名单里的人能驱动;其余人一律拒绝。
  (入站消息驱动一个全权限 agent = 远程命令执行面,白名单是唯一的门。务必设。)

配置(读 .env / 环境变量)
--------
  DINGTALK_APP_KEY / DINGTALK_APP_SECRET   企业内部机器人的凭证(必填)
  DINGTALK_ALLOWED_STAFF                    允许的 staffId,逗号分隔(强烈建议填)
  CLAUDE_WORKDIR                            Claude 的工作目录(默认=仓库根)
  CLAUDE_BIN / CLAUDE_MODEL / CLAUDE_TIMEOUT  可选:claude 路径 / 指定模型 / 超时秒(默认1800)

跑:  python3 tools/dingtalk_bridge/bridge.py
"""

import json
import logging
import fcntl
import os
import subprocess
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

from dingtalk_stream import (
    AckMessage,
    AsyncChatbotHandler,
    ChatbotMessage,
    Credential,
    DingTalkStreamClient,
)

ROOT = Path(__file__).resolve().parents[2]          # 仓库根


def _load_dotenv() -> None:
    """极简 .env 加载(不引 python-dotenv,保持本工具零业务依赖)。"""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.split(" #", 1)[0]                      # 剥掉 ` #` 行内注释(标准 dotenv 行为)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

APP_KEY = os.environ.get("DINGTALK_APP_KEY", "")
APP_SECRET = os.environ.get("DINGTALK_APP_SECRET", "")
ALLOWED = {s.strip() for s in os.environ.get("DINGTALK_ALLOWED_STAFF", "").split(",") if s.strip()}
WORKDIR = os.environ.get("CLAUDE_WORKDIR", str(ROOT))
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "")
TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "1800"))
PROGRESS_INTERVAL = int(os.environ.get("CLAUDE_PROGRESS_INTERVAL", "10"))  # 进度推送最小间隔(秒),防刷屏/限流
MAXLEN = 3500                                        # 单条钉钉消息切片长度(留余量,防超限)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ding-bridge")

# 常驻会话在下方 ClaudeSession 里(一个进程=一段不断累积的上下文)

# ── 消息去重 ──
# 钉钉 Stream 是"至少投递一次":长任务时连接抖动重连,同一条消息可能被重投 → 同一句被执行多次。
# 按 message_id 记住最近处理过的,重复的直接跳过(check+add 原子,防并发重投)。
_seen_ids: deque = deque()
_seen_set: set = set()
_seen_lock = threading.Lock()
_SEEN_MAX = 500


def _is_duplicate(mid) -> bool:
    """这条 message_id 之前处理过吗?没处理过则登记并返回 False。"""
    if not mid:
        return False
    with _seen_lock:
        if mid in _seen_set:
            return True
        _seen_set.add(mid)
        _seen_ids.append(mid)
        while len(_seen_ids) > _SEEN_MAX:            # 环形淘汰,set 与 deque 同步
            _seen_set.discard(_seen_ids.popleft())
        return False


def _chunks(text: str, n: int = MAXLEN):
    """把长回答切成 ≤n 的片,尽量在换行处断开。"""
    text = text or "(无内容)"
    while len(text) > n:
        cut = text.rfind("\n", 0, n)
        if cut < n // 2:
            cut = n
        yield text[:cut]
        text = text[cut:].lstrip("\n")
    if text:
        yield text


def _child_env() -> dict:
    """给 claude 子进程的环境 —— 关键:补齐 reclaude 代理设置。

    reclaude 用『本地 MITM 代理 + 自签 CA』把 claude 的 API 流量导到它的网关。
    交互式 shell 由 reclaude 的 daemon 注入了 HTTP(S)_PROXY + NODE_EXTRA_CA_CERTS;
    但 subprocess 直接调 claude 二进制会**绕过这些注入** → claude 直连 Anthropic、
    却带着 reclaude 的 token → 401 Invalid bearer token(就是之前那个报错)。

    这里从 ~/.reclaude/state.json 动态读 daemon 当前端口(端口每次可能变),补上代理与 CA。
    非 reclaude 机器(无这些文件)则原样跳过,走 claude 自带登录,不受影响。"""
    env = os.environ.copy()
    home = Path.home()
    ca = home / ".reclaude" / "ca.pem"
    if ca.exists():
        env.setdefault("NODE_EXTRA_CA_CERTS", str(ca))
    try:
        st = json.loads((home / ".reclaude" / "state.json").read_text(encoding="utf-8"))
        d = st.get("daemon") or {}
        if d.get("running") and d.get("port"):
            proxy = f"http://127.0.0.1:{d['port']}"
            for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
                env.setdefault(k, proxy)
            for k in ("NO_PROXY", "no_proxy"):
                env.setdefault(k, "localhost,127.0.0.1,::1")
    except Exception:
        pass
    return env


def _fmt_tool(b: dict) -> str:
    """把一次工具调用压成一行人话进度,如「🔧 Bash: pytest -q」「🔧 Read: src/api.py」。"""
    name = b.get("name", "tool")
    inp = b.get("input") or {}
    hint = (inp.get("command") or inp.get("file_path") or inp.get("path")
            or inp.get("pattern") or inp.get("url") or inp.get("description") or "")
    hint = str(hint).replace("\n", " ").strip()[:100]
    return f"🔧 {name}: {hint}" if hint else f"🔧 {name}"


class _Throttle:
    """把频繁的进度行合并,最多每 interval 秒推一条钉钉,避免刷屏/触发限流。
    首条立即发(让你马上看到动起来了),之后按节流批量发。"""

    def __init__(self, send, interval: int = PROGRESS_INTERVAL):
        self._send = send
        self._interval = interval
        self._buf: list = []
        self._last = 0.0
        self._lock = threading.Lock()

    def add(self, line: str) -> None:
        with self._lock:
            self._buf.append(line)
            now = time.time()
            if now - self._last >= self._interval:
                self._flush(now)

    def _flush(self, now: float) -> None:
        if not self._buf:
            return
        body = "\n\n".join(self._buf)[:MAXLEN]
        self._buf = []
        self._last = now
        try:
            self._send(body)
        except Exception as e:
            log.error("进度推送失败: %s", e)

    def stop(self) -> None:
        # 不再单独发尾部缓冲:最终结果紧接着就发,免得两条挤一起。
        with self._lock:
            self._buf = []


class ClaudeSession:
    """常驻的单个 claude 进程(stream-json 输入/输出)。
    一个进程 = 一段不断累积的上下文(跨钉钉消息记得前面聊过什么),就像一个一直开着的终端会话。

    · 串行:一次只处理一轮(turn),读到 result 事件即本轮结束,再处理下一条消息。
    · 每一步(工具调用/说明)既回调 on_progress 推钉钉、也 log 到终端 → 你能在终端旁观。
    · 进程若崩了/超时,下轮自动重启;重启时用捕获到的 session_id `--resume` 续上上下文。
    """

    def __init__(self):
        self._proc = None
        self._sid = None                              # 捕获到的会话id,重启时续上
        self._lock = threading.Lock()                 # 串行:一次一轮

    def busy(self) -> bool:
        return self._lock.locked()

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _start(self) -> None:
        cmd = [CLAUDE_BIN, "-p", "--input-format", "stream-json",
               "--output-format", "stream-json", "--verbose",
               "--dangerously-skip-permissions"]
        if CLAUDE_MODEL:
            cmd += ["--model", CLAUDE_MODEL]
        if self._sid:
            cmd += ["--resume", self._sid]            # 重启续上上次上下文
        self._proc = subprocess.Popen(cmd, cwd=WORKDIR, stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                      text=True, bufsize=1, env=_child_env())
        log.info("🟢 常驻 claude 会话已启动 pid=%s%s", self._proc.pid,
                 f"(resume {self._sid[:8]})" if self._sid else "")

    def _kill(self) -> None:
        if self._proc:
            for act in (lambda: self._proc.stdin.close(), self._proc.terminate):
                try:
                    act()
                except Exception:
                    pass
        self._proc = None

    def reset(self) -> None:
        """开新会话:杀掉进程 + 清空上下文,下轮全新重启。"""
        with self._lock:
            self._sid = None
            self._kill()

    def ask(self, prompt: str, on_progress=None) -> str:
        """喂一条消息,流式读到本轮 result,返回最终回答。全程串行、超时看门狗兜底。"""
        with self._lock:
            if not self._alive():
                try:
                    self._start()
                except FileNotFoundError:
                    return f"❌ 找不到 claude 可执行文件({CLAUDE_BIN})。设 CLAUDE_BIN 指向绝对路径。"
            killer = threading.Timer(TIMEOUT, self._kill)   # 本轮超时 → 强杀,读循环随即 EOF
            killer.start()
            try:
                self._write(prompt)
                return self._read_until_result(on_progress)
            except (BrokenPipeError, OSError):
                self._proc = None
                return "⚠️ 会话进程已断开,已重置。再发一次消息会自动重启。"
            finally:
                killer.cancel()

    def _write(self, text: str) -> None:
        msg = {"type": "user", "message": {"role": "user",
               "content": [{"type": "text", "text": text}]}}
        self._proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def _read_until_result(self, on_progress) -> str:
        # 延迟一步发文字:攒着最近一段 assistant 文本,等下个事件到了才确认它是"中间说明"并发出。
        # 最终答案后面紧跟 result → 永远留在 pending 里被丢弃,只由 ✅ 结果发一次,避免重复。
        pending = None

        def flush():
            nonlocal pending
            if pending:
                self._emit("💬 " + pending, on_progress)
                pending = None

        for line in self._proc.stdout:                 # 逐行读 NDJSON 事件流
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            t = ev.get("type")
            if ev.get("session_id"):
                self._sid = ev["session_id"]           # 记下会话id(重启续上)
            if t == "assistant":
                for b in ev.get("message", {}).get("content", []):
                    if b.get("type") == "text" and b.get("text", "").strip():
                        flush()                        # 前一段确认是中间说明 → 发
                        pending = b["text"].strip()[:300]   # 本段先攒(可能就是最终答案)
                    elif b.get("type") == "tool_use":
                        flush()                        # 工具前的说明 → 发
                        self._emit(_fmt_tool(b), on_progress)
            elif t == "result":
                # 故意不 flush:pending 就是最终答案,交给 ✅ 结果统一发,不重复
                if ev.get("is_error"):
                    return f"❌ {ev.get('result') or ev.get('subtype') or '未知错误'}"
                return ev.get("result") or "(无内容)"
        # 读到 EOF 还没 result = 进程死了(超时被杀/崩溃)
        self._proc = None
        return f"⏱️ 本轮未拿到结果(可能超时{TIMEOUT}s或进程异常),已重置会话。再发一次试试。"

    @staticmethod
    def _emit(line: str, on_progress) -> None:
        log.info(line)                                 # 镜像到终端:你能旁观
        if on_progress:
            on_progress(line)                          # 推钉钉


_session = ClaudeSession()                             # 全局唯一常驻会话


class ClaudeHandler(AsyncChatbotHandler):
    # ★ 必须继承 AsyncChatbotHandler(不是 ChatbotHandler):
    #   它的 raw_process 把同步 process 丢线程池跑、并**立即回 ack**。
    #   若继承 ChatbotHandler,基类会 `await self.process()` —— 我们的 process 是同步返回 tuple,
    #   `await tuple` 会抛错 → ack 发不出去 → 钉钉重投 → 双回复(之前的病根)。
    def process(self, callback):                     # 同步方法,已在框架线程池里跑

        msg = ChatbotMessage.from_dict(callback.data)
        staff = getattr(msg, "sender_staff_id", None)
        text = self._text_of(msg)
        # ★ 去重要用钉钉**稳定的 msgId**(ChatbotMessage.message_id),不能用帧头 message_id ——
        #   帧头是"每次投递"的id,重投会变,按它去重永远失效(就是之前双回复的根因)。
        mid = getattr(msg, "message_id", None)
        frame_id = getattr(getattr(callback, "headers", None), "message_id", None)
        log.info("收到 msgId=%s frameId=%s sender=%s text=%r", mid, frame_id, staff, (text or "")[:40])
        if _is_duplicate(mid):                        # 同一 msgId 处理过 → 跳过,别再跑一遍
            log.info("↳ 跳过重复(msgId 已处理过)")
            return AckMessage.STATUS_OK, "ok"

        # ── 安全闸:白名单 ──
        if ALLOWED and staff not in ALLOWED:
            self.reply_text(f"⛔ 未授权(staffId={staff})。请管理员把你加入 DINGTALK_ALLOWED_STAFF。", msg)
            return AckMessage.STATUS_OK, "ok"
        if not ALLOWED:
            log.warning("未设 DINGTALK_ALLOWED_STAFF —— 任何人都能驱动全权限 agent,危险!")

        if not text:
            self.reply_text("发点文字给我吧。/reset 重开会话,/help 看说明。", msg)
            return AckMessage.STATUS_OK, "ok"

        # ── 内置命令 ──
        low = text.strip().lower()
        if low in ("/reset", "新会话", "重开"):
            _session.reset()
            self.reply_text("🧹 已开新会话(常驻进程重启,上下文清空)。", msg)
            return AckMessage.STATUS_OK, "ok"
        if low in ("/help", "帮助"):
            self.reply_markdown("Claude 桥", _HELP, msg)
            return AckMessage.STATUS_OK, "ok"

        # ── 交给常驻会话(串行 + 实时进度)──
        self.reply_text("⏳ 前一条还在跑,排到你了会继续…" if _session.busy()
                        else "🤔 收到,开始干活…(会实时汇报进度)", msg)
        progress = _Throttle(lambda body: self.reply_markdown("⏳ 进行中", body, msg))
        try:
            reply = _session.ask(text, on_progress=progress.add)
        except Exception as e:
            log.exception("session.ask 异常")
            reply = f"❌ 处理异常: {e}"
        finally:
            progress.stop()
        for chunk in _chunks(reply):
            try:
                self.reply_markdown("✅ Claude", chunk, msg)
            except Exception as e:
                log.error("回复失败: %s", e)
        return AckMessage.STATUS_OK, "ok"

    @staticmethod
    def _text_of(msg) -> str:
        """从消息取纯文本,兼容不同 msgtype。"""
        t = getattr(msg, "text", None)
        if t is not None and getattr(t, "content", None):
            return t.content.strip()
        try:
            parts = msg.get_text_list()
            if parts:
                return "".join(parts).strip()
        except Exception:
            pass
        return ""


_HELP = (
    "**钉钉 ↔ Claude Code 桥**\n\n"
    "- 直接 @我 说人话,我会在仓库里干活(全权限,和你终端一样)再把结果发回来。\n"
    "- 这是**一个常驻会话**:跨消息记得前面聊过什么(就像一直开着的终端),同时镜像到终端可旁观。\n"
    "- 长任务会**实时汇报进度**(🔧 在跑什么命令/读什么文件、💬 我的思路),完成时发 ✅ 结果。\n"
    "- `/reset` 或「新会话」：重启常驻进程、清空上下文,重开一段对话。\n"
    "- `/help`：本说明。\n\n"
    f"工作目录：`{WORKDIR}`"
)


_lock_fd = None                                      # 持有单实例锁的 fd(全程不释放)


def _acquire_singleton() -> None:
    """单实例锁:防止同时起了第二个桥 —— 两个进程各一条 Stream 连接会让同一条消息被执行两次。"""
    global _lock_fd
    _lock_fd = open(os.path.join(tempfile.gettempdir(), "dingtalk_bridge.lock"), "w")
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit("已有一个 bridge 实例在运行(单实例锁)。先停掉旧的再启动:\n"
                         "  pkill -f tools/dingtalk_bridge/bridge.py")
    _lock_fd.write(str(os.getpid()))
    _lock_fd.flush()


def main() -> None:
    if not APP_KEY or not APP_SECRET:
        raise SystemExit("缺 DINGTALK_APP_KEY / DINGTALK_APP_SECRET,请写进 .env")
    _acquire_singleton()
    log.info("工作目录=%s | 白名单=%s | 模型=%s", WORKDIR, ALLOWED or "(未设,危险)", CLAUDE_MODEL or "默认")
    client = DingTalkStreamClient(Credential(APP_KEY, APP_SECRET))
    client.register_callback_handler(ChatbotMessage.TOPIC, ClaudeHandler())
    log.info("已连接钉钉 Stream,等待消息…(@机器人 发消息试试)")
    client.start_forever()


if __name__ == "__main__":
    main()
