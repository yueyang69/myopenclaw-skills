#!/usr/bin/env python3
"""
auto-task-runner v5.1 - Layer 1 调度层

核心升级：
  - call_skill() 改用 StuckAwareRunner，解决 openclaw 流式卡住问题
  - 启动时拉起 Watchdog + Notifier 守护线程
  - tasks.json 新增 retry_count / stuck_reason / stuck_at / deferred 状态
  - resource_guard 升级为跨平台内存检测 + 任务前预检
  - v5.1: 新增文件锁（竞态条件修复）+ 快照读取修复
"""

import json
import os
import sys
import gc
import time
import subprocess
import fcntl
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager

# ===== 路径配置 =====
SKILL_DIR        = Path(__file__).parent.parent
RUNTIME_DIR      = SKILL_DIR / ".runtime"
CHECKPOINT_DIR   = RUNTIME_DIR / "checkpoints"
LOGS_DIR         = RUNTIME_DIR / "logs"
TASKS_FILE       = RUNTIME_DIR / "tasks.json"
STATE_FILE       = RUNTIME_DIR / "state_snapshot.json"
MODEL_USAGE_FILE = RUNTIME_DIR / "model_usage.json"

WORKSPACE             = Path(os.environ.get("WORKSPACE", "/home/admin/.openclaw/workspace"))
DINGTALK_WEBHOOK      = os.environ.get("DINGTALK_WEBHOOK", "")
STEP_INTERVAL_SECONDS = int(os.environ.get("STEP_INTERVAL", "30"))
TASK_INTERVAL_SECONDS = int(os.environ.get("QUEUE_TASK_INTERVAL", "60"))
HEARTBEAT_INTERVAL_MIN = int(os.environ.get("HEARTBEAT_MINUTES", "8"))

# 卡住检测参数（可通过环境变量覆盖）
NO_OUTPUT_TIMEOUT = int(os.environ.get("NO_OUTPUT_TIMEOUT", "600"))   # 10 分钟
HARD_TIMEOUT      = int(os.environ.get("HARD_TIMEOUT",      "5400"))  # 90 分钟
MAX_RETRIES       = int(os.environ.get("MAX_RETRIES",       "2"))

for _d in [CHECKPOINT_DIR, LOGS_DIR]:
    _d.mkdir(exist_ok=True, parents=True)

sys.path.insert(0, str(Path(__file__).parent))


# ===== 文件锁（竞态条件修复 #1）=====
LOCK_FILE = RUNTIME_DIR / "tasks.lock"

@contextmanager
def tasks_lock(exclusive: bool = True):
    """
    tasks.json 文件锁，防止多进程并发读写导致数据损坏。
    
    Args:
        exclusive: True=写锁（排他），False=读锁（共享）
    
    用法：
        with tasks_lock(exclusive=True):
            tasks = _load_tasks_unlocked()
            tasks.append(new_task)
            _save_tasks_unlocked(tasks)
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


# ===== 日志 =====
def log(msg: str):
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    log_file = LOGS_DIR / "task-runner.log"
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ===== 状态持久化 =====
def _write_state(state: dict):
    STATE_FILE.parent.mkdir(exist_ok=True, parents=True)
    STATE_FILE.write_text(
        json.dumps({"timestamp": datetime.now().isoformat(), "state": state},
                   ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8")).get("state", {})
    except Exception:
        return {}


# ===== tasks.json =====
def _load_tasks_unlocked() -> list:
    """
    无锁版本：必须在 tasks_lock() 上下文内调用。
    """
    if TASKS_FILE.exists():
        try:
            return json.loads(TASKS_FILE.read_text(encoding="utf-8")).get("tasks", [])
        except Exception:
            pass
    return []


def _save_tasks_unlocked(tasks: list):
    """
    无锁版本：必须在 tasks_lock() 上下文内调用。
    """
    RUNTIME_DIR.mkdir(exist_ok=True, parents=True)
    TASKS_FILE.write_text(
        json.dumps({"updated_at": datetime.now().isoformat(),
                    "total": len(tasks), "tasks": tasks},
                   ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def _load_tasks() -> list:
    """
    带锁版本：安全读取 tasks.json。
    """
    with tasks_lock(exclusive=False):
        return _load_tasks_unlocked()


def _save_tasks(tasks: list):
    """
    带锁版本：安全写入 tasks.json。
    """
    with tasks_lock(exclusive=True):
        _save_tasks_unlocked(tasks)


def _update_task(tasks: list, task_id: int, **kwargs) -> list:
    for t in tasks:
        if t["id"] == task_id:
            t.update(kwargs)
            break
    return tasks


# ===== 两步任务判断 =====
def judge_task(description: str) -> tuple:
    from router import route
    return route(description)


# ===== 直接执行 shell =====
def direct_exec(cmd: str, task_id: int) -> dict:
    log(f"   🖥️  直接执行：{cmd}")
    cp_file = CHECKPOINT_DIR / f"task_{task_id}_direct.json"
    try:
        result = subprocess.run(
            cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=300
        )
        output  = (result.stdout + result.stderr).strip()
        success = result.returncode == 0
        cp = {"task_id": task_id, "cmd": cmd, "success": success,
              "output": output[:2000], "executed_at": datetime.now().isoformat()}
        cp_file.write_text(json.dumps(cp, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"   {'✅' if success else '❌'} returncode={result.returncode}")
        return cp
    except subprocess.TimeoutExpired:
        cp = {"task_id": task_id, "cmd": cmd, "success": False,
              "output": "超时(300s)", "executed_at": datetime.now().isoformat()}
        cp_file.write_text(json.dumps(cp, ensure_ascii=False, indent=2), encoding="utf-8")
        log("   ❌ 执行超时")
        return cp


# ===== 调用 skill（接入 StuckAwareRunner）=====
def call_skill(skill_name: str, task_desc: str, task_id: int,
               tasks: list, notifier) -> dict:
    log(f"   🔧 调用 skill：{skill_name}")
    cp_file = CHECKPOINT_DIR / f"task_{task_id}_skill.json"

    # 读取 skill_registry
    registry_file = SKILL_DIR / "config" / "skill_registry.json"
    try:
        registry = json.loads(registry_file.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"   ⚠️  读取 skill_registry 失败：{e}")
        registry = {}

    entry = registry.get(skill_name)
    if not entry:
        log(f"   ⚠️  未找到 skill '{skill_name}'，改为直接执行")
        return direct_exec(task_desc, task_id)

    script_path = Path(entry["script"])
    extra_args  = entry.get("args", [])
    cmd         = [sys.executable, str(script_path)] + extra_args + [task_desc]

    # ---- 使用 StuckAwareRunner ----
    from stuck_runner import StuckAwareRunner

    runner = StuckAwareRunner(
        no_output_timeout=NO_OUTPUT_TIMEOUT,
        hard_timeout=HARD_TIMEOUT,
        max_retries=MAX_RETRIES
    )

    def on_stuck(tid, attempt, reason):
        log(f"   ⚠️  任务 [{tid}] 卡住（第{attempt+1}次）：{reason}")
        _update_task(tasks, tid, status="retrying",
                     retry_count=attempt + 1,
                     stuck_reason=reason,
                     stuck_at=datetime.now().isoformat())
        _save_tasks(tasks)
        notifier.task_stuck(tid, attempt, reason)

    def on_retry(tid, attempt):
        log(f"   🔄 任务 [{tid}] 开始第 {attempt} 次重试")
        _update_task(tasks, tid, status="running")
        _save_tasks(tasks)

    result = runner.run_with_retry(
        cmd=cmd,
        task_id=task_id,
        on_stuck=on_stuck,
        on_retry=on_retry
    )

    success = result.get("success", False)
    stuck   = result.get("stuck", False)
    exhausted = result.get("exhausted", False)
    output  = result.get("output", "")[:3000]
    reason  = result.get("reason", "")

    if exhausted:
        log(f"   ❌ skill 多次卡住，已放弃：{reason}")
        notifier.task_stuck_skip(task_id, reason)
    elif stuck:
        log(f"   ❌ skill 卡住：{reason}")
    else:
        log(f"   {'✅' if success else '❌'} skill 返回 returncode={result.get('returncode', 'N/A')}")

    cp = {
        "task_id": task_id, "skill": skill_name,
        "success": success, "stuck": stuck, "exhausted": exhausted,
        "output": output, "reason": reason,
        "attempt": result.get("attempt", 0),
        "executed_at": datetime.now().isoformat()
    }
    cp_file.write_text(json.dumps(cp, ensure_ascii=False, indent=2), encoding="utf-8")
    return cp


# ===== 心跳（保留用于 state_snapshot）=====
class Heartbeat:
    def __init__(self, interval_min: int = 8):
        self.interval  = interval_min * 60
        self.last_beat = datetime.now()

    def due(self) -> bool:
        return (datetime.now() - self.last_beat).total_seconds() >= self.interval

    def beat(self, state: dict):
        log("💓 心跳：状态落盘 + GC")
        _write_state(state)
        collected = gc.collect()
        from resource_guard import check_memory
        check_memory()
        log(f"   GC 回收 {collected} 对象")
        self.last_beat = datetime.now()


# ===== 队列执行核心 =====
def run_queue(tasks: list):
    """
    串行执行任务队列。
    启动时同时拉起 Watchdog + Notifier 守护线程。
    """
    from notifier import Notifier
    from watchdog import Watchdog
    from resource_guard import pre_check, check_disk

    # 启动辅助线程
    notifier = Notifier(webhook=DINGTALK_WEBHOOK, log_fn=log)
    notifier.start()

    watchdog = Watchdog(notifier=notifier, log_fn=log)
    watchdog.start()

    hb      = Heartbeat(HEARTBEAT_INTERVAL_MIN)
    pending = [t for t in tasks if t["status"] in ("pending", "running", "retrying")]
    total   = len(pending)

    if total == 0:
        log("没有待执行的任务")
        notifier.stop()
        watchdog.stop()
        return

    log(f"🚀 开始执行队列：共 {total} 个任务")
    task_list_str = "\n".join([f"- [{t['id']}] {t['description'][:40]}" for t in pending])
    notifier.queue_start(total, task_list_str)

    results = []
    for idx, task in enumerate(pending):
        # 心跳
        if hb.due():
            hb.beat({"current_task_id": task["id"], "tasks": tasks})

        log(f"\n{'='*60}")
        log(f"[{idx+1}/{total}] {task['description']}")
        log(f"{'='*60}")

        # 资源预检
        check_result = pre_check(task["id"], notifier=notifier, log_fn=log)
        if check_result == "critical":
            log("❌ 内存极度不足，主动退出，由 pm2 重启")
            _write_state({"current_task_id": task["id"], "tasks": tasks})
            notifier.stop()
            watchdog.stop()
            sys.exit(1)
        elif check_result == "deferred":
            log(f"   ⏭️  任务 [{task['id']}] 因内存不足延后")
            tasks = _update_task(tasks, task["id"], status="deferred")
            _save_tasks(tasks)
            results.append({"task": task, "success": False,
                            "elapsed": "0秒", "skipped": True})
            continue

        check_disk(RUNTIME_DIR, log_fn=log)

        # 标记 running
        tasks = _update_task(tasks, task["id"],
                             status="running",
                             started_at=datetime.now().isoformat(),
                             retry_count=task.get("retry_count", 0))
        _save_tasks(tasks)

        # 两步任务判断
        action, target = judge_task(task["description"])
        tasks = _update_task(tasks, task["id"], route=f"{action}:{target}")
        log(f"   判断结果：{action} → {target}")

        # 执行
        start_time = datetime.now()
        if action == "direct":
            cp = direct_exec(target, task["id"])
        else:
            cp = call_skill(target, task["description"], task["id"], tasks, notifier)

        elapsed_sec = int((datetime.now() - start_time).total_seconds())
        elapsed_str = f"{elapsed_sec // 60}分{elapsed_sec % 60}秒"
        success     = cp.get("success", False)
        exhausted   = cp.get("exhausted", False)

        final_status = "completed" if success else ("stuck" if exhausted else "error")
        tasks = _update_task(tasks, task["id"],
                             status=final_status,
                             finished_at=datetime.now().isoformat(),
                             elapsed=elapsed_str,
                             result=cp.get("output", "")[:200])
        _save_tasks(tasks)
        notifier.task_complete(task["id"], task["description"][:40], elapsed_str, success)
        results.append({"task": task, "success": success,
                        "elapsed": elapsed_str, "skipped": False})

        # 任务间 GC + 等待
        if idx < total - 1:
            log(f"   ⏸️  任务间隔 {TASK_INTERVAL_SECONDS}s，GC 清理内存...")
            gc.collect()
            time.sleep(TASK_INTERVAL_SECONDS)

    # 汇总通知
    success_count = sum(1 for r in results if r["success"])
    skipped_count = sum(1 for r in results if r.get("skipped"))
    fail_count    = total - success_count - skipped_count
    lines = [f"**队列执行完毕：{success_count}/{total} 成功**\n"]
    for r in results:
        t    = r["task"]
        if r.get("skipped"):
            icon = "⏭️"
        elif r["success"]:
            icon = "✅"
        else:
            icon = "❌"
        lines.append(f"{icon} [{t['id']}] {t['description'][:35]}  耗时：{r['elapsed']}")
    notifier.queue_complete(success_count, total, "\n".join(lines))
    log("🎉 队列全部完成")

    # 停止辅助线程
    watchdog.stop()
    notifier.stop()
    notifier.join(timeout=35)   # 等最后一次 flush 发完


# ===== 命令行入口 =====
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("""
用法：
  python task-runner.py queue add "任务描述"    添加任务到队列
  python task-runner.py queue list              查看队列
  python task-runner.py queue start             启动执行队列
  python task-runner.py queue clear             清除待执行任务
  python task-runner.py queue reset             完全清空队列
""")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "queue":
        if len(sys.argv) < 3:
            print("用法：python task-runner.py queue <add|list|start|clear|reset>")
            sys.exit(1)

        sub = sys.argv[2]

        if sub == "add":
            if len(sys.argv) < 4:
                print("请提供任务描述")
                sys.exit(1)
            task_desc = " ".join(sys.argv[3:])
            tasks     = _load_tasks()
            task_id   = max([t["id"] for t in tasks], default=0) + 1
            new_task  = {
                "id":          task_id,
                "description": task_desc,
                "status":      "pending",
                "route":       None,
                "retry_count": 0,
                "max_retries": MAX_RETRIES,
                "stuck_reason": None,
                "stuck_at":    None,
                "added_at":    datetime.now().isoformat(),
                "started_at":  None,
                "finished_at": None,
                "elapsed":     None,
                "result":      None
            }
            tasks.append(new_task)
            _save_tasks(tasks)
            log(f"✅ 已加入队列 [{task_id}]：{task_desc}")
            print(f"当前队列共 {len(tasks)} 个任务")

        elif sub == "list":
            tasks = _load_tasks()
            if not tasks:
                print("队列为空")
                sys.exit(0)
            print(f"\n{'─'*70}")
            print(f"任务队列 ({len(tasks)} 个任务)")
            print(f"{'─'*70}")
            status_icons = {
                "pending":   "⏳",
                "running":   "🔄",
                "retrying":  "🔁",
                "completed": "✅",
                "error":     "❌",
                "stuck":     "🔴",
                "deferred":  "⏭️"
            }
            for t in tasks:
                icon        = status_icons.get(t["status"], "?")
                elapsed_str = f"  耗时:{t['elapsed']}" if t.get("elapsed") else ""
                retry_str   = f"  重试:{t['retry_count']}次" if t.get("retry_count") else ""
                print(f"  [{t['id']}] {icon} {t['description'][:50]}{elapsed_str}{retry_str}")
            print(f"{'─'*70}\n")

        elif sub == "start":
            tasks = _load_tasks()
            run_queue(tasks)

        elif sub == "clear":
            tasks  = _load_tasks()
            before = len(tasks)
            tasks  = [t for t in tasks if t["status"] not in ("pending", "deferred")]
            _save_tasks(tasks)
            log(f"已清除 {before - len(tasks)} 个待执行任务")

        elif sub == "reset":
            _save_tasks([])
            log("队列已完全清空")

        else:
            print(f"未知子命令：{sub}")
            sys.exit(1)

    else:
        print(f"未知命令：{cmd}")
        sys.exit(1)
