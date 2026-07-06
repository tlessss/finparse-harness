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
    ChatbotHandler,
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

_sessions: dict = {}                                 # staff_id -> claude session_id
_run_lock = threading.Lock()                         # 串行:一次只跑一个 claude

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


def _run_claude(prompt: str, staff_id: str, on_progress=None, allow_resume: bool = True) -> str:
    """流式跑 headless claude(全权限)。逐事件回调 on_progress(实时进度),返回最终回答。
    失败/超时返回可读错误串。"""
    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--dangerously-skip-permissions"]
    if CLAUDE_MODEL:
        cmd += ["--model", CLAUDE_MODEL]
    sid = _sessions.get(staff_id) if allow_resume else None
    if sid:
        cmd += ["--resume", sid]
    try:
        proc = subprocess.Popen(cmd, cwd=WORKDIR, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1,
                                env=_child_env())
    except FileNotFoundError:
        return f"❌ 找不到 claude 可执行文件({CLAUDE_BIN})。设 CLAUDE_BIN 指向绝对路径。"

    killer = threading.Timer(TIMEOUT, proc.kill)      # 看门狗:超时强杀,防流卡死
    killer.start()
    final = None
    try:
        for line in proc.stdout:                       # 逐行读 NDJSON 事件流
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            t = ev.get("type")
            if t == "assistant":
                for b in ev.get("message", {}).get("content", []):
                    if b.get("type") == "text" and b.get("text", "").strip():
                        if on_progress:
                            on_progress("💬 " + b["text"].strip()[:300])   # 它的说明/思路
                    elif b.get("type") == "tool_use":
                        if on_progress:
                            on_progress(_fmt_tool(b))                       # 它在干的动作
            elif t == "result":
                final = ev
    finally:
        killer.cancel()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    if final is None:
        return f"⏱️ 未拿到结果(可能超时{TIMEOUT}s被中断)。可发 /reset 重开会话,或把任务拆小。"
    if final.get("session_id"):
        _sessions[staff_id] = final["session_id"]      # 记住会话,下条续上
    if final.get("is_error"):
        # 常见:--resume 的旧会话失效 → 丢掉会话重试一次(全新上下文)
        if sid and allow_resume:
            _sessions.pop(staff_id, None)
            log.warning("resume 失败,丢会话重试")
            return _run_claude(prompt, staff_id, on_progress, allow_resume=False)
        return f"❌ {final.get('result') or final.get('subtype') or '未知错误'}"
    return final.get("result") or "(无内容)"


class ClaudeHandler(ChatbotHandler):
    def process(self, callback):                     # 注意:框架要求同步方法,已在线程池里跑
        mid = getattr(getattr(callback, "headers", None), "message_id", None)
        if _is_duplicate(mid):                        # 重投的同一条 → 跳过,别再跑一遍
            log.info("跳过重复消息 message_id=%s", mid)
            return AckMessage.STATUS_OK, "ok"
        msg = ChatbotMessage.from_dict(callback.data)
        staff = getattr(msg, "sender_staff_id", None)
        text = self._text_of(msg)

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
            _sessions.pop(staff, None)
            self.reply_text("🧹 已开新会话(上下文清空)。", msg)
            return AckMessage.STATUS_OK, "ok"
        if low in ("/help", "帮助"):
            self.reply_markdown("Claude 桥", _HELP, msg)
            return AckMessage.STATUS_OK, "ok"

        # ── 交给 Claude(串行 + 实时进度)──
        busy = _run_lock.locked()
        self.reply_text("⏳ 前一条还在跑,排到你了会继续…" if busy else "🤔 收到,开始干活…(会实时汇报进度)", msg)
        progress = _Throttle(lambda body: self.reply_markdown("⏳ 进行中", body, msg))
        with _run_lock:
            try:
                reply = _run_claude(text, staff, on_progress=progress.add)
            except Exception as e:
                log.exception("run_claude 异常")
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
    "- 长任务会**实时汇报进度**(🔧 在跑什么命令/读什么文件、💬 我的思路),完成时发 ✅ 结果。\n"
    "- `/reset` 或「新会话」：清空上下文,重开一段对话。\n"
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
