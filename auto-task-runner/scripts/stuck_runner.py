#!/usr/bin/env python3
"""
stuck_runner.py - 卡住感知执行器

替换 subprocess.run，用 Popen + 独立读取线程实现：
  - 实时监控子进程输出
  - 超过 NO_OUTPUT_TIMEOUT 无新输出 → 判定卡住 → kill
  - 超过 HARD_TIMEOUT 总时限 → 强制 kill
  - 自动重试，重试耗尽后标记 stuck
"""

import gc
import json
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from subprocess import PIPE, STDOUT
from typing import Callable, Optional

SKILL_DIR     = Path(__file__).parent.parent
RUNTIME_DIR   = SKILL_DIR / ".runtime"
HEARTBEAT_DIR = RUNTIME_DIR / "heartbeat"


def _write_heartbeat(task_id: int, pid: int, last_output_at: float, retry_count: int):
    """写心跳文件，供 Watchdog 监控"""
    HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
    hb = {
        "task_id": task_id,
        "pid": pid,
        "last_output_at": datetime.fromtimestamp(last_output_at).isoformat(),
        "retry_count": retry_count,
        "updated_at": datetime.now().isoformat()
    }
    hb_file = HEARTBEAT_DIR / f"task_{task_id}.json"
    try:
        hb_file.write_text(json.dumps(hb, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _clear_heartbeat(task_id: int):
    """任务结束后清理心跳文件"""
    hb_file = HEARTBEAT_DIR / f"task_{task_id}.json"
    try:
        hb_file.unlink(missing_ok=True)
    except Exception:
        pass


class StuckAwareRunner:
    """
    卡住感知执行器。

    用法：
        runner = StuckAwareRunner()
        result = runner.run_with_retry(
            cmd=["python3", "skill/main.py", "task desc"],
            task_id=1,
            on_stuck=lambda tid, attempt, reason: print(f"卡住了: {reason}"),
            on_retry=lambda tid, attempt: print(f"第{attempt}次重试")
        )
        # result: {"success": bool, "stuck": bool, "exhausted": bool, "output": str}
    """

    def __init__(
        self,
        no_output_timeout: int = 600,   # 10 分钟无输出 → 卡住
        hard_timeout: int = 5400,       # 90 分钟总时限
        max_retries: int = 2,           # 最多重试 2 次（共执行 3 次）
        retry_wait_base: int = 60,      # 重试等待基数（秒），每次翻倍
        heartbeat_interval: int = 30,   # 心跳写入间隔（秒）
    ):
        self.no_output_timeout  = no_output_timeout
        self.hard_timeout       = hard_timeout
        self.max_retries        = max_retries
        self.retry_wait_base    = retry_wait_base
        self.heartbeat_interval = heartbeat_interval

    # ------------------------------------------------------------------
    # 单次执行（不含重试）
    # ------------------------------------------------------------------
    def run(self, cmd: list, task_id: int, retry_count: int = 0) -> dict:
        """
        启动子进程，实时监控输出。

        返回：
          {"success": bool, "stuck": bool, "reason": str, "output": str, "returncode": int}
        """
        last_output   = [time.time()]   # list 使闭包可写
        last_hb_write = [time.time()]
        output_lines  = []
        lock          = threading.Lock()

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=PIPE,
                stderr=STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
        except Exception as e:
            return {
                "success": False, "stuck": False,
                "reason": f"启动进程失败: {e}",
                "output": "", "returncode": -1
            }

        # 独立线程：实时读取输出
        def _reader():
            try:
                for line in proc.stdout:
                    with lock:
                        output_lines.append(line)
                        now = time.time()
                        last_output[0] = now
                        # 限频写心跳，避免频繁 IO
                        if now - last_hb_write[0] >= self.heartbeat_interval:
                            _write_heartbeat(task_id, proc.pid, now, retry_count)
                            last_hb_write[0] = now
            except Exception:
                pass

        reader = threading.Thread(target=_reader, daemon=True)
        reader.start()
        start_time = time.time()

        # 初始写一次心跳
        _write_heartbeat(task_id, proc.pid, start_time, retry_count)

        # 主循环：每秒轮询
        while True:
            time.sleep(1)

            returncode = proc.poll()
            if returncode is not None:          # ① 进程已退出
                reader.join(timeout=10)
                _clear_heartbeat(task_id)
                return {
                    "success": returncode == 0,
                    "stuck": False,
                    "reason": "",
                    "output": "".join(output_lines),
                    "returncode": returncode
                }

            with lock:
                no_output_secs = time.time() - last_output[0]
            elapsed_secs = time.time() - start_time

            if no_output_secs > self.no_output_timeout:   # ② 卡住
                proc.kill()
                reader.join(timeout=5)
                _clear_heartbeat(task_id)
                return {
                    "success": False, "stuck": True,
                    "reason": f"{no_output_secs:.0f}s 无输出（阈值 {self.no_output_timeout}s）",
                    "output": "".join(output_lines),
                    "returncode": -1
                }

            if elapsed_secs > self.hard_timeout:          # ③ 超总时限
                proc.kill()
                reader.join(timeout=5)
                _clear_heartbeat(task_id)
                return {
                    "success": False, "stuck": True,
                    "reason": f"超过总时限 {self.hard_timeout}s",
                    "output": "".join(output_lines),
                    "returncode": -1
                }

    # ------------------------------------------------------------------
    # 带重试的执行
    # ------------------------------------------------------------------
    def run_with_retry(
        self,
        cmd: list,
        task_id: int,
        on_stuck: Optional[Callable] = None,
        on_retry: Optional[Callable] = None,
    ) -> dict:
        """
        执行命令，卡住自动重试。

        返回结果新增字段：
          exhausted: bool  — True 表示重试次数耗尽，任务应标记 stuck
          attempt:   int   — 最终是第几次尝试（0-based）
        """
        for attempt in range(self.max_retries + 1):
            result = self.run(cmd, task_id, retry_count=attempt)
            result["attempt"] = attempt
            result["exhausted"] = False

            if not result["stuck"]:
                return result           # 正常结束（不管成功或失败）

            # 卡住了
            if on_stuck:
                try:
                    on_stuck(task_id, attempt, result["reason"])
                except Exception:
                    pass

            if attempt >= self.max_retries:
                result["exhausted"] = True
                return result           # 重试耗尽

            # 等待后重试
            wait_secs = self.retry_wait_base * (attempt + 1)  # 60s, 120s
            gc.collect()
            time.sleep(wait_secs)

            if on_retry:
                try:
                    on_retry(task_id, attempt + 1)
                except Exception:
                    pass

        return result   # 不可达，max_retries >= 0 保证至少执行一次


# ------------------------------------------------------------------
# 独立测试
# ------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    print("=== StuckAwareRunner 测试 ===")

    # 测试 1：正常执行
    print("\n[测试1] 正常命令（echo）")
    runner = StuckAwareRunner(no_output_timeout=5, hard_timeout=30)
    res = runner.run([sys.executable, "-c", "print('hello'); import time; time.sleep(1); print('done')"],
                    task_id=999)
    print(f"  success={res['success']}, stuck={res['stuck']}, output={res['output'].strip()!r}")

    # 测试 2：卡住检测
    print("\n[测试2] 卡住命令（sleep 60），5s 无输出超时")
    runner2 = StuckAwareRunner(no_output_timeout=5, hard_timeout=30, max_retries=1)
    res2 = runner2.run_with_retry(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        task_id=998,
        on_stuck=lambda tid, att, reason: print(f"  [回调] 任务{tid} 第{att}次卡住：{reason}"),
        on_retry=lambda tid, att: print(f"  [回调] 任务{tid} 开始第{att}次重试")
    )
    print(f"  success={res2['success']}, stuck={res2['stuck']}, exhausted={res2['exhausted']}")
    print(f"  reason={res2['reason']!r}")

