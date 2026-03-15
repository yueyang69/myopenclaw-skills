#!/usr/bin/env python3
"""
notifier.py - 异步通知队列

职责：
  - 主流程调用 enqueue() 写入本地队列文件（不阻塞）
  - 独立发送线程每 30s 轮询，批量发送到钉钉
  - 发送失败自动重试，超过 3 次丢弃
  - 网络不可用时完全不阻塞任务执行
"""

import json
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

SKILL_DIR        = Path(__file__).parent.parent
RUNTIME_DIR      = SKILL_DIR / ".runtime"
NOTIFY_QUEUE_FILE = RUNTIME_DIR / "notify_queue.json"

MAX_FAIL_COUNT   = 3      # 失败超过此次数直接丢弃
SEND_INTERVAL    = 30     # 发送线程轮询间隔（秒）
HTTP_TIMEOUT     = 8      # 单次 HTTP 超时（秒）


class Notifier:
    """
    异步通知器。

    用法：
        notifier = Notifier(webhook="https://oapi.dingtalk.com/robot/send?access_token=xxx")
        notifier.start()

        # 在任务流程中随时调用，不阻塞：
        notifier.enqueue("task_stuck", "任务卡住", "任务 [2] 卡住了，准备重试")

        notifier.stop()
    """

    def __init__(self, webhook: str = "", log_fn=None):
        self.webhook   = webhook
        self.log       = log_fn or print
        self._stop_evt = threading.Event()
        self._lock     = threading.Lock()
        self._thread   = threading.Thread(
            target=self._loop, daemon=True, name="Notifier"
        )

    def start(self):
        """启动后台发送线程"""
        self.log("📬 Notifier 启动")
        self._thread.start()

    def stop(self):
        """通知发送线程停止，并尝试把队列里的消息发完"""
        self._stop_evt.set()

    def join(self, timeout: float = 5.0):
        self._thread.join(timeout=timeout)

    # ------------------------------------------------------------------
    # 入队（主流程调用，不阻塞）
    # ------------------------------------------------------------------
    def enqueue(self, event: str, title: str, content: str,
                is_error: bool = False):
        """
        把一条通知写入本地队列文件。
        即使钉钉不可用，此操作也不会失败阻塞。
        """
        msg = {
            "id":         f"{int(time.time()*1000)}",
            "event":      event,
            "title":      title,
            "content":    content,
            "is_error":   is_error,
            "created_at": datetime.now().isoformat(),
            "fail_count": 0
        }
        with self._lock:
            queue = self._load_queue()
            queue.append(msg)
            self._save_queue(queue)

    # ------------------------------------------------------------------
    # 发送线程主循环
    # ------------------------------------------------------------------
    def _loop(self):
        while not self._stop_evt.wait(timeout=SEND_INTERVAL):
            self._flush()
        # 停止前再 flush 一次
        self._flush()
        self.log("📬 Notifier 已停止")

    def _flush(self):
        """尝试发送队列中所有消息"""
        if not self.webhook:
            return

        with self._lock:
            queue = self._load_queue()

        if not queue:
            return

        remaining = []
        for msg in queue:
            success = self._send(msg)
            if success:
                self.log(f"📱 通知发送成功：{msg['event']} - {msg['title']}")
            else:
                msg["fail_count"] += 1
                if msg["fail_count"] < MAX_FAIL_COUNT:
                    remaining.append(msg)   # 还有重试机会
                else:
                    self.log(f"⚠️  通知丢弃（超过{MAX_FAIL_COUNT}次失败）：{msg['title']}")

        with self._lock:
            self._save_queue(remaining)

    def _send(self, msg: dict) -> bool:
        """发送单条消息到钉钉，返回是否成功"""
        try:
            emoji   = "❌" if msg.get("is_error") else "✅"
            title   = msg["title"]
            content = msg["content"]
            ts      = msg["created_at"][:19].replace("T", " ")

            text = f"## {emoji} {title}\n\n{content}\n\n> {ts}"
            payload = json.dumps({
                "msgtype": "markdown",
                "markdown": {"title": title, "text": text}
            }).encode("utf-8")

            req = urllib.request.Request(
                self.webhook,
                data=payload,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                result = json.loads(resp.read())
                return result.get("errcode") == 0

        except Exception as e:
            self.log(f"⚠️  通知发送异常：{e}")
            return False

    # ------------------------------------------------------------------
    # 队列持久化
    # ------------------------------------------------------------------
    def _load_queue(self) -> list:
        """从文件加载队列（调用方须持锁）"""
        if not NOTIFY_QUEUE_FILE.exists():
            return []
        try:
            return json.loads(NOTIFY_QUEUE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save_queue(self, queue: list):
        """保存队列到文件（调用方须持锁）"""
        try:
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            NOTIFY_QUEUE_FILE.write_text(
                json.dumps(queue, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            self.log(f"⚠️  保存通知队列失败：{e}")

    # ------------------------------------------------------------------
    # 便捷方法（语义化事件）
    # ------------------------------------------------------------------
    def queue_start(self, total: int, task_list: str):
        self.enqueue("queue_start",
                     f"任务队列启动：{total} 个任务",
                     f"{task_list}\n\n去忙吧，完事通知你 👋")

    def queue_complete(self, success: int, total: int, summary: str):
        failed = total - success
        self.enqueue("queue_complete",
                     "任务队列完成" if failed == 0 else f"任务队列完成（{failed}个失败）",
                     summary,
                     is_error=(failed > 0))

    def task_stuck(self, task_id: int, attempt: int, reason: str):
        self.enqueue("task_stuck",
                     f"任务 [{task_id}] 卡住，准备重试",
                     f"卡住原因：{reason}\n这是第 {attempt+1} 次尝试，自动重试中...",
                     is_error=True)

    def task_stuck_skip(self, task_id: int, reason: str):
        self.enqueue("task_stuck_skip",
                     f"任务 [{task_id}] 多次卡住，已跳过",
                     f"卡住原因：{reason}\n已超过最大重试次数，请人工检查。",
                     is_error=True)

    def task_complete(self, task_id: int, desc: str, elapsed: str, success: bool):
        self.enqueue("task_complete",
                     f"任务 [{task_id}] {'完成' if success else '失败'}",
                     f"{desc}\n耗时：{elapsed}",
                     is_error=not success)

    def watchdog_recover(self, task_id: int, reason: str, retry_count: int):
        self.enqueue("watchdog_recover",
                     f"Watchdog：任务 [{task_id}] 已重新入队",
                     f"原因：{reason}\n第 {retry_count} 次恢复，继续执行中...")

    def memory_low(self, task_id: int, mem_mb: float):
        self.enqueue("memory_low",
                     f"内存不足，任务 [{task_id}] 已延后",
                     f"当前可用内存：{mem_mb:.0f}MB，任务暂缓执行，等待内存释放。",
                     is_error=True)


# ------------------------------------------------------------------
# 独立测试
# ------------------------------------------------------------------
if __name__ == "__main__":
    from datetime import datetime

    def fake_log(msg):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    print("=== Notifier 测试（无 webhook，验证队列写入）===")
    n = Notifier(webhook="", log_fn=fake_log)
    n.start()

    n.enqueue("test", "测试通知", "这是一条测试消息")
    n.task_stuck(1, 0, "612s 无输出")
    n.queue_complete(3, 4, "任务1 ✅ 任务2 ✅ 任务3 ✅ 任务4 ❌")

    # 检查队列文件
    if NOTIFY_QUEUE_FILE.exists():
        queue = json.loads(NOTIFY_QUEUE_FILE.read_text(encoding="utf-8"))
        print(f"\n队列文件中有 {len(queue)} 条消息：")
        for q in queue:
            print(f"  [{q['event']}] {q['title']}")
    else:
        print("队列文件未创建（可能 webhook 为空被跳过）")

    n.stop()
    n.join()
    print("\nNotifier 测试完成")

