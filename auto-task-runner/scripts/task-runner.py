#!/usr/bin/env python3.10
"""
自动任务执行器 v3.2 - 员工模式

新增：
- 员工模式触发词：「员工模式」
- 任务确认对话：AI复述理解，用户补充后才开始
- 员工日志：每次执行在 employee-logs/YYYY-MM-DD/ 下生成报告
"""

import json
import os
import sys
import gc
import time
import re
import urllib.request
from datetime import datetime
from pathlib import Path

# ===== 配置 =====
# SKILL 自身目录（存放运行时数据，不污染工作区根目录）
SKILL_DIR = Path(__file__).parent.parent
RUNTIME_DIR = SKILL_DIR / ".runtime"  # 所有运行时文件统一放这里

CHECKPOINT_DIR = RUNTIME_DIR / "checkpoints"
PLANS_DIR = RUNTIME_DIR / "plans"
REPORTS_DIR = RUNTIME_DIR / "reports"
LOGS_DIR = RUNTIME_DIR / "logs"
STATE_SNAPSHOT_FILE = RUNTIME_DIR / "state_snapshot.json"

# WORKSPACE 仅用于 executor 执行任务时的工作目录
WORKSPACE = Path(os.environ.get("WORKSPACE", "/home/admin/.openclaw/workspace"))

# 钉钉 Webhook（从环境变量读取，或直接填写）
DINGTALK_WEBHOOK = os.environ.get("DINGTALK_WEBHOOK", "")

# 步骤间隔（秒）- 每步完成后等待，让内存喘口气
STEP_INTERVAL_SECONDS = int(os.environ.get("STEP_INTERVAL", "30"))

# 心跳间隔（分钟）
HEARTBEAT_INTERVAL_MINUTES = int(os.environ.get("HEARTBEAT_MINUTES", "8"))

for d in [CHECKPOINT_DIR, PLANS_DIR, REPORTS_DIR, LOGS_DIR]:
    d.mkdir(exist_ok=True, parents=True)

sys.path.insert(0, str(Path(__file__).parent))


# ===== 日志 =====
def log(message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    log_file = LOGS_DIR / "task-runner.log"
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(line + "\n")


# ===== 钉钉通知 =====
def notify_dingtalk(title: str, content: str, is_error: bool = False):
    """发送钉钉通知（Markdown 格式）"""
    if not DINGTALK_WEBHOOK:
        log("⚠️  未配置 DINGTALK_WEBHOOK，跳过通知")
        return

    emoji = "❌" if is_error else "✅"
    text = f"## {emoji} {title}\n\n{content}\n\n> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    payload = json.dumps({
        "msgtype": "markdown",
        "markdown": {"title": title, "text": text}
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            DINGTALK_WEBHOOK,
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("errcode") == 0:
                log("📱 钉钉通知发送成功")
            else:
                log(f"⚠️  钉钉通知失败：{result}")
    except Exception as e:
        log(f"⚠️  钉钉通知异常：{e}")


# ===== 心跳管理 =====
class HeartbeatManager:
    def __init__(self, interval_minutes: int = 8):
        self.interval = interval_minutes * 60
        self.last_heartbeat = datetime.now()

    def check(self) -> bool:
        elapsed = (datetime.now() - self.last_heartbeat).total_seconds()
        return elapsed >= self.interval

    def trigger(self, state: dict):
        log("💓 心跳触发 - 状态落盘 + GC")
        snapshot = {"timestamp": datetime.now().isoformat(), "state": state}
        STATE_SNAPSHOT_FILE.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        collected = gc.collect()
        mem = _get_available_memory_mb()
        log(f"   GC 回收 {collected} 个对象，可用内存 {mem:.0f}MB")
        if mem < 300:
            log("⚠️  内存紧张 (<300MB)，触发额外清理")
            gc.collect()
        self.last_heartbeat = datetime.now()


def _get_available_memory_mb() -> float:
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemAvailable:'):
                    return int(line.split()[1]) / 1024
    except:
        pass
    return 999.0


# ===== 主编排器 =====
class TaskOrchestrator:
    def __init__(self):
        self.heartbeat = HeartbeatManager(HEARTBEAT_INTERVAL_MINUTES)
        self.state = {
            "task_description": None,
            "plan": None,
            "current_step": 0,
            "completed_steps": [],
            "failed_steps": [],
            "status": "idle",
            "started_at": None
        }

    def run_task(self, task_description: str, force_restart: bool = False):
        """主入口：接收自然语言任务描述，自动分解并执行"""
        log(f"🚀 任务开始：{task_description}")
        self.state["task_description"] = task_description
        self.state["status"] = "running"
        self.state["started_at"] = datetime.now().isoformat()

        # 崩溃恢复
        if not force_restart and STATE_SNAPSHOT_FILE.exists():
            self._load_state()
            saved_task = self.state.get("task_description", "")
            if saved_task == task_description and self.state.get("plan"):
                log(f"📌 恢复之前进度：已完成步骤 {self.state['completed_steps']}")
            else:
                # 新任务，重置状态
                self.state.update({
                    "plan": None, "current_step": 0,
                    "completed_steps": [], "failed_steps": []
                })

        try:
            # 阶段1：建筑师 - 动态分解任务
            if not self.state.get("plan"):
                log("📐 阶段1：qwen + claude 协作分解任务...")
                from architect import generate_plan
                self.state["plan"] = generate_plan(task_description)
                self._save_state()

            steps = self.state["plan"].get("steps", [])
            if not steps:
                raise ValueError("任务分解失败，未生成任何步骤")

            log(f"📋 共 {len(steps)} 个步骤，开始执行")
            notify_dingtalk(
                f"任务开始：{task_description[:30]}",
                f"已拆解为 **{len(steps)}** 个步骤，开始执行，完成后通知你。\n\n不用盯着，去忙吧 👋"
            )

            # 标记最后一步（触发 claude 最终验收）
            if steps:
                steps[-1]["is_last"] = True

            # 阶段2：执行循环
            self._run_execution_loop(steps)

            # 完成汇报
            total = len(steps)
            done = len(self.state["completed_steps"])
            failed = len(self.state["failed_steps"])
            self.state["status"] = "completed" if failed == 0 else "partial"
            self._save_state()

            # 生成摘要
            elapsed = self._get_elapsed_time()
            summary_lines = [
                f"**任务：** {task_description[:50]}",
                f"**结果：** 完成 {done}/{total} 步",
                f"**耗时：** {elapsed}",
            ]
            if failed > 0:
                summary_lines.append(f"**失败步骤：** {self.state['failed_steps']}")
                # 列出失败步骤名称
                failed_names = [
                    s['name'] for s in steps if s['id'] in self.state['failed_steps']
                ]
                summary_lines.append(f"**失败详情：** {', '.join(failed_names)}")

            summary = "\n".join(summary_lines)
            log(f"🎉 全部完成\n{summary}")
            notify_dingtalk(
                "任务完成" if failed == 0 else "任务部分完成",
                summary,
                is_error=(failed > 0)
            )

        except Exception as e:
            log(f"💥 任务异常：{e}")
            import traceback
            log(traceback.format_exc())
            self.state["status"] = "error"
            self._save_state()
            notify_dingtalk(
                f"任务异常中断",
                f"**任务：** {task_description[:50]}\n\n**错误：** {str(e)[:300]}",
                is_error=True
            )
            raise

    def _run_execution_loop(self, steps: list):
        """执行循环：逐步执行 + 验证 + 心跳"""
        from executor import execute_step_local
        from verifier import verify_step

        total = len(steps)

        while self.state["current_step"] < total:
            # 心跳检查
            if self.heartbeat.check():
                self.heartbeat.trigger(self.state)

            step = steps[self.state["current_step"]]

            # 跳过已完成（崩溃恢复）
            if step["id"] in self.state["completed_steps"]:
                log(f"⏭️  步骤 {step['id']} 已完成，跳过")
                self.state["current_step"] += 1
                continue

            # 检查依赖
            if not self._check_dependencies(step):
                log(f"🔒 步骤 {step['id']} 依赖未满足，标记失败")
                self.state["failed_steps"].append(step["id"])
                self.state["current_step"] += 1
                continue

            log(f"▶️  [{self.state['current_step']+1}/{total}] {step['name']}")

            # 执行步骤（qwen 主力）
            exec_result = execute_step_local(step)

            # 验证结果（claude 关键节点验证）
            verdict = verify_step(step, exec_result)
            decision = verdict.get("verifier_decision", "failed")

            if decision == "done":
                log(f"   ✅ 完成")
                self.state["completed_steps"].append(step["id"])
            elif decision == "retry":
                log(f"   🔄 需要重试，再执行一次")
                exec_result2 = execute_step_local(step)
                verdict2 = verify_step(step, exec_result2)
                if verdict2.get("verifier_decision") == "done":
                    log(f"   ✅ 重试成功")
                    self.state["completed_steps"].append(step["id"])
                else:
                    log(f"   ❌ 重试失败")
                    self.state["failed_steps"].append(step["id"])
            else:
                log(f"   ❌ 失败：{verdict.get('verifier_reason', '')[:80]}")
                self.state["failed_steps"].append(step["id"])

            self.state["current_step"] += 1
            self._save_state()

            # 步骤间隔：让内存喘口气
            if self.state["current_step"] < total:
                log(f"   ⏸️  间隔 {STEP_INTERVAL_SECONDS}s 清理内存...")
                gc.collect()
                time.sleep(STEP_INTERVAL_SECONDS)

    def _check_dependencies(self, step: dict) -> bool:
        """检查步骤依赖是否已完成"""
        for dep_id in step.get("dependencies", []):
            if dep_id not in self.state["completed_steps"]:
                return False
        return True

    def _save_state(self):
        snapshot = {"timestamp": datetime.now().isoformat(), "state": self.state}
        STATE_SNAPSHOT_FILE.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _load_state(self):
        try:
            snapshot = json.loads(STATE_SNAPSHOT_FILE.read_text(encoding="utf-8"))
            self.state = snapshot.get("state", self.state)
        except Exception as e:
            log(f"⚠️  加载状态失败：{e}")

    def _get_elapsed_time(self) -> str:
        try:
            started = datetime.fromisoformat(self.state["started_at"])
            elapsed = datetime.now() - started
            minutes = int(elapsed.total_seconds() // 60)
            seconds = int(elapsed.total_seconds() % 60)
            return f"{minutes}分{seconds}秒"
        except:
            return "未知"


# ===== 任务队列管理 =====
QUEUE_FILE = RUNTIME_DIR / "task_queue.json"


def _load_queue() -> list:
    """加载任务队列"""
    if QUEUE_FILE.exists():
        try:
            return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        except:
            pass
    return []


def _save_queue(queue: list):
    """保存任务队列"""
    RUNTIME_DIR.mkdir(exist_ok=True, parents=True)
    QUEUE_FILE.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")


def queue_add(task_description: str):
    """添加任务到队列"""
    queue = _load_queue()
    task = {
        "id": len(queue) + 1,
        "description": task_description,
        "status": "pending",
        "added_at": datetime.now().isoformat(),
        "started_at": None,
        "finished_at": None,
        "elapsed": None,
        "result": None
    }
    queue.append(task)
    _save_queue(queue)
    print(f"✅ 已加入队列 [{task['id']}]：{task_description}")
    print(f"   当前队列共 {len(queue)} 个任务")


def queue_list():
    """显示任务队列状态"""
    queue = _load_queue()
    if not queue:
        print("队列为空。用 'queue add \"任务描述\"' 添加任务")
        return

    status_icon = {
        "pending": "⏳",
        "running": "🔄",
        "completed": "✅",
        "partial": "⚠️",
        "error": "❌"
    }
    print(f"\n{'─'*60}")
    print(f"任务队列 ({len(queue)} 个任务)")
    print(f"{'─'*60}")
    for t in queue:
        icon = status_icon.get(t["status"], "?")
        elapsed = f"  耗时:{t['elapsed']}" if t.get("elapsed") else ""
        print(f"  [{t['id']}] {icon} {t['description'][:50]}{elapsed}")
    print(f"{'─'*60}\n")


def queue_clear():
    """清空队列（只清 pending 状态的任务）"""
    queue = _load_queue()
    before = len(queue)
    queue = [t for t in queue if t["status"] not in ("pending",)]
    _save_queue(queue)
    print(f"已清除 {before - len(queue)} 个待执行任务，保留 {len(queue)} 个已完成/进行中任务")


def queue_reset():
    """完全清空队列"""
    _save_queue([])
    print("队列已完全清空")


def queue_start():
    """
    启动队列执行：依次执行所有 pending 任务
    任务间休息 60s + GC，保护内存
    全部完成后发钉钉汇总通知
    """
    queue = _load_queue()
    pending = [t for t in queue if t["status"] == "pending"]

    if not pending:
        print("没有待执行的任务。用 'queue add' 先添加任务")
        return

    total = len(pending)
    log(f"🚀 队列启动：共 {total} 个任务")
    log(f"任务列表：")
    for t in pending:
        log(f"  [{t['id']}] {t['description']}")

    # 开始通知
    task_list_str = "\n".join([f"- [{t['id']}] {t['description'][:40]}" for t in pending])
    notify_dingtalk(
        f"任务队列启动：{total} 个任务",
        f"开始依次执行以下任务，完成后统一汇报：\n\n{task_list_str}\n\n去忙吧，完事通知你 👋"
    )

    results = []

    for idx, task in enumerate(pending):
        # 更新队列状态
        task["status"] = "running"
        task["started_at"] = datetime.now().isoformat()
        _update_queue_task(task)

        log(f"\n{'='*60}")
        log(f"[{idx+1}/{total}] 开始任务：{task['description']}")
        log(f"{'='*60}")

        try:
            orchestrator = TaskOrchestrator()
            orchestrator.run_task(task["description"], force_restart=True)

            # 统计结果
            done = len(orchestrator.state["completed_steps"])
            failed = len(orchestrator.state["failed_steps"])
            steps_total = done + failed
            elapsed = orchestrator._get_elapsed_time()

            task["status"] = "completed" if failed == 0 else "partial"
            task["finished_at"] = datetime.now().isoformat()
            task["elapsed"] = elapsed
            task["result"] = f"{done}/{steps_total} 步完成"
            results.append({"task": task, "success": failed == 0})

        except Exception as e:
            elapsed = "未知"
            try:
                elapsed = orchestrator._get_elapsed_time()
            except:
                pass
            task["status"] = "error"
            task["finished_at"] = datetime.now().isoformat()
            task["elapsed"] = elapsed
            task["result"] = f"异常：{str(e)[:100]}"
            results.append({"task": task, "success": False})
            log(f"💥 任务异常：{e}")

        _update_queue_task(task)

        # 任务间隔：清内存，给服务器喘口气
        if idx < total - 1:
            interval = int(os.environ.get("QUEUE_TASK_INTERVAL", "60"))
            log(f"\n⏸️  任务间隔 {interval}s，清理内存后继续...")
            gc.collect()
            time.sleep(interval)

    # 全部完成，发汇总通知
    success_count = sum(1 for r in results if r["success"])
    fail_count = total - success_count

    summary_lines = [f"**队列执行完毕：{success_count}/{total} 成功**\n"]
    for r in results:
        t = r["task"]
        icon = "✅" if r["success"] else ("⚠️" if t["status"] == "partial" else "❌")
        summary_lines.append(f"{icon} [{t['id']}] {t['description'][:35]}")
        summary_lines.append(f"   结果：{t['result']}  耗时：{t['elapsed']}")

    summary = "\n".join(summary_lines)
    log(f"\n🎉 队列全部完成\n{summary}")
    notify_dingtalk(
        "任务队列完成" if fail_count == 0 else f"任务队列完成（{fail_count}个失败）",
        summary,
        is_error=(fail_count > 0)
    )


# ===== 员工模式 =====
EMPLOYEE_LOGS_DIR = RUNTIME_DIR / "employee-logs"


def _parse_employee_tasks(raw_input: str) -> list:
    """
    从用户输入中解析任务列表
    支持格式：
      1. 任务描述
      2. 任务描述
    或：
      - 任务描述
      - 任务描述
    """
    tasks = []
    lines = raw_input.strip().split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 匹配 "1. xxx" 或 "- xxx" 或 "* xxx"
        m = re.match(r'^[\d]+[.、。)）]\s*(.+)$', line) or \
            re.match(r'^[-*•]\s*(.+)$', line)
        if m:
            tasks.append(m.group(1).strip())
        elif line and not re.match(r'^(员工模式|小智员工|今天你要完成)', line):
            # 没有编号但也不是开场白，也算任务
            tasks.append(line)
    return [t for t in tasks if len(t) > 3]  # 过滤太短的


def _employee_confirm(tasks: list) -> list:
    """
    员工确认阶段：AI复述对每个任务的理解，用户补充后确认
    返回确认后的最终任务列表（可能被用户修改过）
    """
    call_model = None
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from model_client import call_model as _call
        call_model = _call
    except ImportError:
        pass

    print("\n" + "="*60)
    print("👋 小智员工收到任务，正在理解中...")
    print("="*60)

    # 用 qwen 对每个任务生成理解摘要
    task_summaries = []
    for i, task in enumerate(tasks, 1):
        if call_model:
            understanding = call_model(
                prompt=f"""用一句话（20字以内）复述你对以下任务的理解，只输出这一句话：
任务：{task}""",
                model="dashscope-coding/qwen3.5-plus",
                label=f"理解任务{i}"
            )
            understanding = understanding.strip().replace('\n', '')
        else:
            understanding = task[:40]
        task_summaries.append(understanding)

    # 打印确认信息
    print("\n📋 收到老板！我来确认一下我的理解：\n")
    for i, (task, summary) in enumerate(zip(tasks, task_summaries), 1):
        print(f"  任务{i}：{summary}")

    print("\n以上理解正确吗？")
    print("- 直接回车 = 确认，开始执行")
    print("- 输入补充说明 = 我会更新理解后开始")
    print("- 输入 '取消' = 取消所有任务")
    print()

    try:
        user_input = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        user_input = ""

    if user_input in ("取消", "cancel", "quit", "q"):
        print("\n已取消。")
        return []

    if user_input:
        # 用户有补充，更新任务描述
        print(f"\n收到补充：{user_input}")
        print("已记录，开始执行...\n")
        # 把补充信息附加到第一个相关任务，或作为全局备注
        tasks = [f"{t}（补充：{user_input}）" if i == 0 else t
                 for i, t in enumerate(tasks)]
    else:
        print("\n✅ 确认！开始执行，完成后钉钉通知你\n")

    return tasks


def employee_mode(raw_input: str):
    """
    员工模式主入口

    流程：
    1. 解析任务列表
    2. AI确认理解（等用户补充）
    3. 批量加入队列并执行
    4. 每个任务生成带日期的报告文件
    5. 钉钉汇总通知
    """
    today = datetime.now().strftime("%Y-%m-%d")
    log_dir = EMPLOYEE_LOGS_DIR / today
    log_dir.mkdir(exist_ok=True, parents=True)

    # 解析任务
    tasks = _parse_employee_tasks(raw_input)
    if not tasks:
        print("未找到任务描述，请检查输入格式")
        print("示例：")
        print("  员工模式")
        print("  1. 清理旧日志")
        print("  2. 检查磁盘空间")
        return

    print(f"\n解析到 {len(tasks)} 个任务")

    # 确认阶段
    tasks = _employee_confirm(tasks)
    if not tasks:
        return

    # 清空旧队列，加入今天的任务
    _save_queue([])
    for t in tasks:
        queue_add(t)

    # 开始执行
    queue = _load_queue()
    pending = [t for t in queue if t["status"] == "pending"]
    total = len(pending)

    log(f"👔 员工模式启动：{today}，共 {total} 个任务")
    task_list_str = "\n".join([f"- [{t['id']}] {t['description'][:40]}" for t in pending])
    notify_dingtalk(
        f"👔 员工今日任务开始 ({total}个)",
        f"日期：{today}\n\n任务列表：\n{task_list_str}\n\n完成后汇报 👋"
    )

    results = []
    for idx, task in enumerate(pending):
        task["status"] = "running"
        task["started_at"] = datetime.now().isoformat()
        _update_queue_task(task)

        log(f"\n{'='*60}")
        log(f"[{idx+1}/{total}] {task['description']}")
        log(f"{'='*60}")

        try:
            orchestrator = TaskOrchestrator()
            orchestrator.run_task(task["description"], force_restart=True)

            done = len(orchestrator.state["completed_steps"])
            failed = len(orchestrator.state["failed_steps"])
            steps_total = done + failed
            elapsed = orchestrator._get_elapsed_time()
            success = (failed == 0)

            task["status"] = "completed" if success else "partial"
            task["finished_at"] = datetime.now().isoformat()
            task["elapsed"] = elapsed
            task["result"] = f"{done}/{steps_total} 步完成"

            # 生成员工日志文件
            _write_employee_log(log_dir, idx + 1, task, orchestrator.state, success)

        except Exception as e:
            import traceback
            elapsed = "未知"
            task["status"] = "error"
            task["finished_at"] = datetime.now().isoformat()
            task["elapsed"] = elapsed
            task["result"] = f"异常：{str(e)[:80]}"
            success = False
            _write_employee_log(log_dir, idx + 1, task, {}, False, error=str(e))
            log(f"💥 任务异常：{e}")

        _update_queue_task(task)
        results.append({"task": task, "success": success})

        if idx < total - 1:
            interval = int(os.environ.get("QUEUE_TASK_INTERVAL", "60"))
            log(f"\n⏸️  任务间隔 {interval}s...")
            gc.collect()
            time.sleep(interval)

    # 生成汇总文件
    _write_employee_summary(log_dir, today, results)

    # 钉钉汇总通知
    success_count = sum(1 for r in results if r["success"])
    fail_count = total - success_count
    lines = [f"**{today} 员工日报：{success_count}/{total} 完成**\n"]
    for r in results:
        t = r["task"]
        icon = "✅" if r["success"] else ("⚠️" if t["status"] == "partial" else "❌")
        lines.append(f"{icon} {t['description'][:35]}")
        lines.append(f"   {t['result']}  耗时:{t['elapsed']}")
    lines.append(f"\n📄 报告：employee-logs/{today}/")

    notify_dingtalk(
        f"👔 员工日报 {today}" + ("" if fail_count == 0 else f"（{fail_count}个失败）"),
        "\n".join(lines),
        is_error=(fail_count > 0)
    )

    print(f"\n📁 所有报告已保存到：{log_dir}")


def _write_employee_log(log_dir: Path, idx: int, task: dict,
                        state: dict, success: bool, error: str = ""):
    """生成单个任务的员工日志文件"""
    safe_name = re.sub(r'[^\w\u4e00-\u9fff]', '-', task['description'])[:20]
    filename = f"task-{idx:02d}-{safe_name}.md"
    filepath = log_dir / filename

    icon = "✅" if success else "❌"
    completed = state.get("completed_steps", [])
    failed = state.get("failed_steps", [])
    plan = state.get("plan", {})
    steps = plan.get("steps", []) if plan else []

    lines = [
        f"# {icon} 任务 {idx}：{task['description']}",
        f"",
        f"| 项目 | 内容 |",
        f"|------|------|",
        f"| 开始时间 | {task.get('started_at', 'N/A')} |",
        f"| 完成时间 | {task.get('finished_at', 'N/A')} |",
        f"| 耗时 | {task.get('elapsed', 'N/A')} |",
        f"| 结果 | {task.get('result', 'N/A')} |",
        f"",
    ]

    if steps:
        lines += ["## 执行步骤", ""]
        for s in steps:
            sid = s.get("id")
            sname = s.get("name", "")
            if sid in completed:
                lines.append(f"- ✅ 步骤{sid}：{sname}")
            elif sid in failed:
                lines.append(f"- ❌ 步骤{sid}：{sname}")
            else:
                lines.append(f"- ⏭️ 步骤{sid}：{sname}（跳过）")
        lines.append("")

    if error:
        lines += ["## 异常信息", "", f"```", error[:500], "```", ""]

    filepath.write_text("\n".join(lines), encoding="utf-8")


def _write_employee_summary(log_dir: Path, today: str, results: list):
    """生成汇总文件 summary.md"""
    success_count = sum(1 for r in results if r["success"])
    total = len(results)

    lines = [
        f"# 员工日报 - {today}",
        f"",
        f"**完成：{success_count}/{total}**",
        f"",
        f"| # | 任务 | 结果 | 耗时 | 状态 |",
        f"|---|------|------|------|------|",
    ]
    for r in results:
        t = r["task"]
        icon = "✅" if r["success"] else ("⚠️" if t["status"] == "partial" else "❌")
        lines.append(
            f"| {t['id']} | {t['description'][:30]} | {t.get('result','N/A')} "
            f"| {t.get('elapsed','N/A')} | {icon} |"
        )
    lines += ["", f"---", f"*生成时间：{datetime.now().isoformat()}*"]

    summary_file = log_dir / "summary.md"
    summary_file.write_text("\n".join(lines), encoding="utf-8")
    log(f"📄 汇总报告：{summary_file}")


def _update_queue_task(updated_task: dict):
    """更新队列中指定任务的状态"""
    queue = _load_queue()
    for i, t in enumerate(queue):
        if t["id"] == updated_task["id"]:
            queue[i] = updated_task
            break
    _save_queue(queue)


# ===== 单任务状态 =====
def list_tasks():
    """列出当前单任务状态"""
    if STATE_SNAPSHOT_FILE.exists():
        snapshot = json.loads(STATE_SNAPSHOT_FILE.read_text(encoding="utf-8"))
        state = snapshot.get("state", {})
        print(f"\n当前任务：{state.get('task_description', '无')}")
        print(f"状态：{state.get('status', '未知')}")
        print(f"已完成步骤：{state.get('completed_steps', [])}")
        print(f"失败步骤：{state.get('failed_steps', [])}")
        print(f"开始时间：{state.get('started_at', '未知')}")
    else:
        print("无任务记录")

    print("\n检查点文件：")
    for cp in sorted(CHECKPOINT_DIR.glob("step_*_verified.json")):
        data = json.loads(cp.read_text(encoding="utf-8"))
        print(f"  步骤 {data['step_id']}: {data.get('step_name','?')} → {data.get('verifier_decision','?')}")


# ===== 入口 =====
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("""
用法：
  # 单任务模式
  python task-runner.py run "任务描述"       执行单个任务
  python task-runner.py restart "任务描述"   从头重新执行
  python task-runner.py list                 查看当前任务状态

  # 队列模式（推荐：早上批量添加，晚上看结果）
  python task-runner.py queue add "任务1"    添加任务到队列
  python task-runner.py queue add "任务2"    继续添加
  python task-runner.py queue list           查看队列
  python task-runner.py queue start          启动执行（然后去忙别的）
  python task-runner.py queue clear          清除待执行任务
  python task-runner.py queue reset          完全清空队列

环境变量：
  DINGTALK_WEBHOOK      钉钉机器人 Webhook URL
  STEP_INTERVAL         步骤间隔秒数（默认30）
  QUEUE_TASK_INTERVAL   队列任务间隔秒数（默认60）
  HEARTBEAT_MINUTES     心跳间隔分钟（默认8）
  WORKSPACE             任务执行工作目录
""")
        sys.exit(1)

    command = sys.argv[1]

    # 员工模式
    if command == "员工模式" or command == "employee":
        # 支持两种方式：
        # 1. 直接在命令行传入：python task-runner.py 员工模式 "1.任务1\n2.任务2"
        # 2. 交互式输入（不带参数时）
        if len(sys.argv) >= 3:
            raw_input = " ".join(sys.argv[2:])
        else:
            print("请输入今天的任务列表（每行一个，用编号）：")
            print("输入完成后按 Ctrl+D（Linux）或 Ctrl+Z（Windows）结束")
            lines = []
            try:
                while True:
                    line = input()
                    lines.append(line)
            except EOFError:
                pass
            raw_input = "\n".join(lines)
        employee_mode(raw_input)

    # 队列命令
    if command == "queue":
        if len(sys.argv) < 3:
            print("用法：python task-runner.py queue <add|list|start|clear|reset>")
            sys.exit(1)
        sub = sys.argv[2]
        if sub == "add":
            if len(sys.argv) < 4:
                print("请提供任务描述：python task-runner.py queue add \"任务描述\"")
                sys.exit(1)
            queue_add(" ".join(sys.argv[3:]))
        elif sub == "list":
            queue_list()
        elif sub == "start":
            queue_start()
        elif sub == "clear":
            queue_clear()
        elif sub == "reset":
            queue_reset()
        else:
            print(f"未知队列子命令：{sub}")
            sys.exit(1)

    # 单任务命令
    elif command == "run":
        if len(sys.argv) < 3:
            print("请提供任务描述")
            sys.exit(1)
        orchestrator = TaskOrchestrator()
        orchestrator.run_task(" ".join(sys.argv[2:]))

    elif command == "restart":
        if len(sys.argv) < 3:
            print("请提供任务描述")
            sys.exit(1)
        orchestrator = TaskOrchestrator()
        orchestrator.run_task(" ".join(sys.argv[2:]), force_restart=True)

    elif command == "list":
        list_tasks()

    else:
        print(f"未知命令：{command}")
        sys.exit(1)
