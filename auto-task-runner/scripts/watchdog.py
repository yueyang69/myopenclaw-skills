#!/usr/bin/env python3
"""
watchdog.py - 看门狗守护线程

职责：
  - 启动时立即扫描一次（处理 pm2 重启后的孤儿 running 任务）
  - 之后每 SCAN_INTERVAL 分钟扫描一次
  - 找出 status=running 但心跳超时的任务 → 强制恢复 → 重新入队

与 StuckAwareRunner 分工：
  StuckAwareRunner → 实时检测输出卡住（进程活但无输出）
  Watchdog         → 检测线程崩溃 / pm2 重启后孤儿任务（心跳文件超时）

v5.1 升级：
  - 新增文件锁（与 task-runner.py 共享锁机制）
  - _recover_task 前重新_load_tasks()，避免多任务快照覆盖
"""

import json
import os
import signal
import threading
import time
import fcntl
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager

SKILL_DIR     = Path(__file__).parent.parent
RUNTIME_DIR   = SKILL_DIR / ".runtime"
HEARTBEAT_DIR = RUNTIME_DIR / "heartbeat"
TASKS_FILE    = RUNTIME_DIR / "tasks.json"
LOCK_FILE     = RUNTIME_DIR / "tasks.lock"

# 心跳超时阈值：超过此时间未刷新，判定任务异常
HEARTBEAT_TIMEOUT_MINUTES = 15
# 扫描间隔
SCAN_INTERVAL_MINUTES = 10


# ===== 文件锁（与 task-runner.py 共享）=====
@contextmanager
def tasks_lock(exclusive: bool = True):
    """
    tasks.json 文件锁，防止多进程并发读写。
    """
    LOCK_FILE.parent.mkdir(exist_ok=True, parents=True)
    lock_fd = None
    try:
        lock_fd = open(LOCK_FILE, "w")
        lock_type = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(lock_fd.fileno(), lock_type)
        yield
    finally:
        if lock_fd:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            lock_fd.close()


class Watchdog:
    """
    看门狗守护线程。

    用法：
        from notifier import Notifier
        notifier = Notifier(...)
        wd = Watchdog(notifier=notifier)
        wd.start()   # 启动后台线程，主进程退出时自动停止
    """

    def __init__(self, notifier=None, log_fn=None):
        """
        notifier : Notifier 实例，用于发送钉钉通知（可为 None）
        log_fn   : 日志函数，签名 log_fn(msg: str)，默认 print
        """
        self.notifier  = notifier
        self.log       = log_fn or print
        self._stop_evt = threading.Event()
        self._thread   = threading.Thread(target=self._loop, daemon=True, name="Watchdog")

    def start(self):
        """启动看门狗线程"""
        self.log("🐶 Watchdog 启动")
        self._thread.start()

    def stop(self):
        """通知看门狗停止（非阻塞）"""
        self._stop_evt.set()

    def join(self, timeout: float = 5.0):
        """等待看门狗线程退出"""
        self._thread.join(timeout=timeout)

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def _loop(self):
        # 启动时立即扫描一次（处理 pm2 重启后的孤儿任务）
        self._scan()

        interval_secs = SCAN_INTERVAL_MINUTES * 60
        while not self._stop_evt.wait(timeout=interval_secs):
            self._scan()

        self.log("🐶 Watchdog 已停止")

    # ------------------------------------------------------------------
    # 扫描逻辑
    # ------------------------------------------------------------------
    def _scan(self):
        self.log("🐶 Watchdog 扫描中...")
        
        # 修复 #7：每次处理前重新加载最新数据，避免快照覆盖
        with tasks_lock(exclusive=False):
            tasks = self._load_tasks_unlocked()
        
        running_tasks = [t for t in tasks if t.get("status") == "running"]

        if not running_tasks:
            self.log("🐶 Watchdog：无 running 任务，跳过")
            return

        # 逐个检查，每次恢复前重新加载最新数据
        for task in running_tasks:
            # 修复 #7：在_check_task 内部重新加载，确保基于最新快照
            self._check_task(task)

    def _check_task(self, task: dict):
        """
        检查单个任务的心跳状态。
        修复 #7：需要恢复时，重新加载最新 tasks 数据，避免快照覆盖。
        """
        task_id = task["id"]
        hb_file = HEARTBEAT_DIR / f"task_{task_id}.json"

        # 读取心跳文件
        if not hb_file.exists():
            started_at = task.get("started_at")
            if started_at:
                try:
                    started_dt = datetime.fromisoformat(started_at)
                    age = (datetime.now() - started_dt).total_seconds() / 60
                    if age < HEARTBEAT_TIMEOUT_MINUTES:
                        return
                except Exception:
                    pass
            self.log(f"🐶 Watchdog：任务 [{task_id}] 无心跳文件，重置为 pending")
            self._recover_task(task_id, pid=None, reason="无心跳文件")
            return

        # 读心跳内容
        try:
            hb = json.loads(hb_file.read_text(encoding="utf-8"))
        except Exception as e:
            self.log(f"🐶 Watchdog：读取心跳失败 task_{task_id}: {e}")
            return

        last_output_at = hb.get("last_output_at", "")
        pid            = hb.get("pid")

        # 检查心跳时间
        try:
            last_dt = datetime.fromisoformat(last_output_at)
        except Exception:
            self.log(f"🐶 Watchdog：心跳时间格式错误 task_{task_id}，重置")
            self._recover_task(task_id, pid=pid, reason="心跳时间格式错误")
            return

        age_minutes = (datetime.now() - last_dt).total_seconds() / 60
        if age_minutes < HEARTBEAT_TIMEOUT_MINUTES:
            self.log(f"🐶 Watchdog：任务 [{task_id}] 心跳正常（{age_minutes:.1f}min 前）")
            return

        # 心跳超时
        self.log(f"🐶 Watchdog：任务 [{task_id}] 心跳超时 {age_minutes:.1f}min，触发恢复")
        self._recover_task(task_id, pid=pid, reason=f"心跳超时 {age_minutes:.0f}min")

    def _recover_task(self, task_id: int, pid, reason: str):
        """
        强制终止进程（如果存活），重置任务状态为 pending。
        修复 #7：恢复前重新加载最新 tasks 数据，避免多任务快照覆盖。
        """
        # 尝试终止进程
        if pid:
            self._kill_pid(pid)

        # 清理心跳文件
        hb_file = HEARTBEAT_DIR / f"task_{task_id}.json"
        try:
            hb_file.unlink(missing_ok=True)
        except Exception:
            pass

        # 修复 #7：重新加载最新数据（加写锁）
        with tasks_lock(exclusive=True):
            tasks = self._load_tasks_unlocked()
            now = datetime.now().isoformat()
            
            for t in tasks:
                if t["id"] == task_id:
                    retry_count = t.get("retry_count", 0) + 1
                    max_retries = t.get("max_retries", 2)

                    if retry_count > max_retries:
                        t["status"] = "stuck"
                        t["stuck_reason"] = f"Watchdog 恢复超过上限：{reason}"
                        t["stuck_at"] = now
                        self.log(f"🐶 Watchdog：任务 [{task_id}] 重试耗尽，标记 stuck")
                        self._notify("task_stuck_skip",
                                     f"任务 [{task_id}] 已放弃",
                                     f"原因：{reason}\n已超过最大重试次数，请人工检查。")
                    else:
                        t["status"] = "pending"
                        t["retry_count"] = retry_count
                        t["stuck_reason"] = reason
                        t["stuck_at"] = now
                        t["started_at"] = None
                        self.log(f"🐶 Watchdog：任务 [{task_id}] 重新入队（第{retry_count}次）")
                        self._notify("watchdog_recover",
                                     f"任务 [{task_id}] 已重新入队",
                                     f"原因：{reason}\n第 {retry_count} 次恢复，继续执行。")
                    break

            self._save_tasks_unlocked(tasks)

    def _kill_pid(self, pid: int):
        """优雅终止进程：先 SIGTERM，等 10s，再 SIGKILL"""
        try:
            # 检查进程是否存活
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return  # 进程不存在，无需处理

        self.log(f"🐶 Watchdog：终止进程 PID={pid}")
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(10)
            # 再次检查
            try:
                os.kill(pid, 0)         # 还活着
                os.kill(pid, signal.SIGKILL)
                self.log(f"🐶 Watchdog：SIGKILL PID={pid}")
            except (ProcessLookupError, PermissionError):
                pass  # SIGTERM 已生效
        except Exception as e:
            self.log(f"🐶 Watchdog：终止进程失败 PID={pid}: {e}")

    # ------------------------------------------------------------------
    # 任务持久化（复用 task-runner 的逻辑，避免循环 import）
    # ------------------------------------------------------------------
    def _load_tasks_unlocked(self) -> list:
        if not TASKS_FILE.exists():
            return []
        try:
            return json.loads(TASKS_FILE.read_text(encoding="utf-8")).get("tasks", [])
        except Exception as e:
            self.log(f"🐶 Watchdog：读取 tasks.json 失败: {e}")
            return []

    def _save_tasks(self, tasks: list):
        try:
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            TASKS_FILE.write_text(
                json.dumps({
                    "updated_at": datetime.now().isoformat(),
                    "total": len(tasks),
                    "tasks": tasks
                }, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            self.log(f"🐶 Watchdog：写入 tasks.json 失败: {e}")

    def _notify(self, event: str, title: str, content: str):
        if self.notifier:
            try:
                self.notifier.enqueue(event, title, content)
            except Exception:
                pass


# ------------------------------------------------------------------
# 独立测试
# ------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    def fake_log(msg):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    wd = Watchdog(log_fn=fake_log)
    wd.start()

    print("Watchdog 已启动，等待 5 秒后停止...")
    time.sleep(5)
    wd.stop()
    wd.join()
    print("Watchdog 已停止")

