# auto-task-runner v5.0 设计框架

> **核心目标**：解决 openclaw 流式执行卡住问题，实现真正的无人值守任务队列。
>
> **硬件约束**：2GB 内存轻量服务器，任务必须串行，严格资源管控。

---

## 一、问题根因分析

### 当前 v4.0 的致命缺陷

openClaw 执行任务时是流式输出，进程长期存活但可能停止产生输出（卡住）。当前代码用 `subprocess.run(timeout=1800)` 等待子进程，这个 timeout 是「进程存活超时」而非「输出无进展超时」。openclaw 卡住时进程仍然存活，30 分钟的 timeout 永远不会触发，整个队列因此永久堵死，必须人工杀进程。

```
openClaw 执行任务（流式）
    ↓
subprocess.run(timeout=1800)   ← 只等进程死，不管有没有输出
    ↓
openClaw 卡住 → 进程活着，就是不输出
    ↓
timeout 永远不触发
    ↓
队列永久堵死 → 人工介入
```

---

## 二、v5.0 架构总览

```
┌─────────────────────────────────────────────────────────┐
│                  Layer 0：守护层（pm2）                   │
│  崩溃重启 + 模型调用次数监控（qwen/claude 限流）            │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│             Layer 1：调度层（task-runner.py）              │
│                                                         │
│  任务队列管理      卡住看门狗         资源守卫             │
│  tasks.json      watchdog.py       resource_guard.py    │
│       ↓               ↓                  ↓             │
│  ┌─────────────────────────────────────────────────┐    │
│  │         执行引擎：StuckAwareRunner                │    │
│  │  Popen + 输出线程监控 + 无进展超时 + 自动重试       │    │
│  └─────────────────────────────────────────────────┘    │
│       ↓               ↓                                 │
│  heartbeat/       notifier.py（异步通知队列）              │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│             Layer 2：执行层（各 skill）                   │
│  dual-expert-chat / academic-deep-research / pdf-toolkit │
│  searxng / pr-reviewer / debug-pro / a-stock / code-mentor│
└─────────────────────────────────────────────────────────┘
```

---

## 三、核心模块设计

### 3.1 StuckAwareRunner（卡住感知执行器）

**用 Popen + 独立读取线程替换 subprocess.run**

关键参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `NO_OUTPUT_TIMEOUT` | 600s（10分钟） | 多久没有新输出视为卡住 |
| `HARD_TIMEOUT` | 5400s（90分钟） | 单任务最大总时长 |
| `MAX_RETRIES` | 2 | 卡住后最多重试次数 |
| `RETRY_WAIT` | 60s | 重试前等待时间 |

执行流程：

```
Popen 启动子进程
    ↓
独立线程实时读取 stdout/stderr
每有新输出 → 刷新 last_output_time + 写心跳文件
    ↓
主线程每秒轮询三个条件：
  ① proc.poll() is not None  → 进程正常结束，记录结果
  ② now - last_output_time > NO_OUTPUT_TIMEOUT  → 卡住，kill
  ③ now - start_time > HARD_TIMEOUT  → 超总时限，kill
    ↓
kill 后根据 retry_count 决定：重试 or 标记 stuck
```

重试策略：

```
第 1 次卡住 → kill → gc.collect() → 等 60s  → status=retrying → 重启
第 2 次卡住 → kill → gc.collect() → 等 120s → status=retrying → 重启
第 3 次卡住 → kill → status=stuck → 跳过，继续下一任务
             → 钉钉通知：任务 [X] 多次卡住已跳过，请人工检查
```

核心代码结构（stuck_runner.py）：

```python
import subprocess, threading, time, gc
from subprocess import PIPE, STDOUT

class StuckAwareRunner:
    def __init__(self, no_output_timeout=600, hard_timeout=5400, max_retries=2):
        self.no_output_timeout = no_output_timeout
        self.hard_timeout = hard_timeout
        self.max_retries = max_retries

    def run(self, cmd: list, task_id: int, retry_count: int = 0) -> dict:
        last_output = [time.time()]   # 用列表使闭包可写
        output_lines = []

        proc = subprocess.Popen(cmd, stdout=PIPE, stderr=STDOUT, text=True)

        def _reader():
            for line in proc.stdout:
                output_lines.append(line)
                last_output[0] = time.time()
                _write_heartbeat(task_id, proc.pid, last_output[0], retry_count)

        reader = threading.Thread(target=_reader, daemon=True)
        reader.start()
        start_time = time.time()

        while True:
            time.sleep(1)
            if proc.poll() is not None:
                reader.join(timeout=5)
                return {
                    "success": proc.returncode == 0,
                    "stuck": False,
                    "output": "".join(output_lines)
                }
            no_output_secs = time.time() - last_output[0]
            elapsed_secs   = time.time() - start_time

            if no_output_secs > self.no_output_timeout:
                proc.kill()
                return {"success": False, "stuck": True,
                        "reason": f"{no_output_secs:.0f}s 无输出"}
            if elapsed_secs > self.hard_timeout:
                proc.kill()
                return {"success": False, "stuck": True,
                        "reason": f"超过总时限 {self.hard_timeout}s"}

    def run_with_retry(self, cmd: list, task_id: int,
                       on_stuck=None, on_retry=None) -> dict:
        for attempt in range(self.max_retries + 1):
            result = self.run(cmd, task_id, retry_count=attempt)
            if not result.get("stuck"):
                return result          # 正常结束（成功或失败）
            # 卡住了
            if on_stuck:
                on_stuck(task_id, attempt, result["reason"])
            if attempt >= self.max_retries:
                result["exhausted"] = True
                return result          # 重试耗尽
            wait = 60 * (attempt + 1)
            gc.collect()
            time.sleep(wait)
            if on_retry:
                on_retry(task_id, attempt + 1)
        return result
```

---

### 3.2 Watchdog（看门狗守护线程）

独立守护线程，弥补 StuckAwareRunner 覆盖不到的场景：执行线程自身崩溃、pm2 重启后孤儿任务。

**两者分工：**

| 场景 | 谁处理 |
|------|--------|
| openclaw 输出卡住（进程活但无输出） | StuckAwareRunner 实时检测 |
| 执行线程崩溃（状态停在 running） | Watchdog 定时扫描 |
| pm2 重启后发现孤儿 running 任务 | Watchdog 启动时立即扫描 |

**Watchdog 逻辑：**

```
启动时立即扫描一次（处理 pm2 重启后的孤儿任务）
之后每 10 分钟扫描一次
    ↓
找出 status=running 的任务
    ↓
读取 .runtime/heartbeat/{task_id}.json
检查 last_output_at 距今是否超过 15 分钟
    ↓
超时 → 检查 PID 是否存活
  存活  → SIGTERM，等 10s，再 SIGKILL
  不存活 → 直接重置状态
    ↓
retry_count += 1，status=pending，重新入队
钉钉通知：Watchdog 检测到任务 [X] 无心跳，已重新入队（第N次）
```

**心跳文件格式（.runtime/heartbeat/{task_id}.json）：**

```json
{
  "task_id": 2,
  "pid": 12345,
  "last_output_at": "2026-03-15T10:31:00",
  "retry_count": 0,
  "updated_at": "2026-03-15T10:31:00"
}
```

---

### 3.3 任务状态机（v5.0）

```
pending
  ↓
running ──────────────→ completed
  │
  ├──→ error          （returncode != 0，非卡住，不重试）
  │
  └──→ retrying       （卡住，等待重试中）
         ↓
       running        （重试开始）
         ↓
       stuck          （重试耗尽，需人工处理）
```

tasks.json 新增字段：

```json
{
  "id": 2,
  "description": "调研主流向量数据库",
  "status": "retrying",
  "retry_count": 1,
  "max_retries": 2,
  "stuck_reason": "612s 无输出",
  "stuck_at": "2026-03-15T10:32:00",
  "route": "skill:academic-deep-research",
  "added_at": "2026-03-15T08:00:00",
  "started_at": "2026-03-15T10:20:00",
  "finished_at": null,
  "elapsed": null,
  "result": null
}
```

---

### 3.4 资源守卫升级（resource_guard.py）

**问题**：`get_available_memory_mb()` 读 `/proc/meminfo`，仅 Linux 有效；Windows/macOS 返回 999，形同虚设。

**修复方案（优先级降级）：**

```python
def get_available_memory_mb() -> float:
    # 1. psutil（跨平台首选）
    try:
        import psutil
        return psutil.virtual_memory().available / 1024 / 1024
    except ImportError:
        pass
    # 2. Linux
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemAvailable:'):
                    return int(line.split()[1]) / 1024
    except Exception:
        pass
    # 3. macOS
    try:
        out = subprocess.check_output(['vm_stat'], text=True)
        for line in out.splitlines():
            if 'Pages free' in line:
                pages = int(line.split(':')[1].strip().rstrip('.'))
                return pages * 4096 / 1024 / 1024
    except Exception:
        pass
    # 4. Windows
    try:
        out = subprocess.check_output(
            ['wmic', 'OS', 'get', 'FreePhysicalMemory'], text=True)
        kb = int([l for l in out.splitlines() if l.strip().isdigit()][0])
        return kb / 1024
    except Exception:
        pass
    return 999.0   # 无法检测，跳过
```

**新增：任务启动前资源预检**

```
任务启动前调用 pre_check(task_id)
    ↓
可用内存 < 300MB
  → 等 60s，重检，最多 3 次
  → 3 次后仍不足 → status=deferred，跳过该任务
  → 钉钉通知：内存不足，任务 [X] 已延后

可用内存 < 150MB
  → 立即写 checkpoint，主动退出
  → pm2 重启后 Watchdog 恢复
```

---

### 3.5 通知系统（notifier.py）

**问题**：当前同步发送，网络超时 10s 会阻塞主流程，失败不重试。

**升级：异步通知队列**

```
主流程调用 notify(event, title, content)
    ↓
追加写入 .runtime/notify_queue.json（不阻塞主流程）
    ↓
独立发送线程每 30s 轮询队列
    ↓
成功发送 → 从队列删除
失败      → fail_count += 1，超过 3 次直接丢弃
```

**通知事件类型：**

| 事件 | 触发时机 |
|------|----------|
| `queue_start` | 队列开始执行 |
| `task_stuck` | 单次卡住，准备重试 |
| `task_stuck_skip` | 重试耗尽，已跳过 |
| `task_complete` | 单个任务完成 |
| `queue_complete` | 队列全部完成 |
| `watchdog_recover` | Watchdog 触发恢复 |
| `memory_low` | 内存不足，任务延后 |

---

## 四、文件结构（v5.0）

```
auto-task-runner/
├── scripts/
│   ├── task-runner.py        # 主入口 + 调度核心（改造 call_skill）
│   ├── stuck_runner.py       # ★新增：StuckAwareRunner
│   ├── watchdog.py           # ★新增：看门狗守护线程
│   ├── notifier.py           # ★新增：异步通知队列
│   ├── router.py             # 路由器（不变）
│   └── resource_guard.py     # 升级：跨平台内存检测 + 预检
├── config/
│   ├── router.json           # 路由规则
│   └── skill_registry.json   # skill 注册表
├── .runtime/
│   ├── tasks.json            # 任务队列（含重试字段）
│   ├── state_snapshot.json   # 心跳快照
│   ├── model_usage.json      # 模型调用次数
│   ├── notify_queue.json     # ★新增：待发送通知队列
│   ├── heartbeat/            # ★新增：各任务心跳文件
│   │   └── task_{id}.json
│   ├── checkpoints/          # 各任务执行结果
│   └── logs/
│       └── task-runner.log
├── DESIGN.md
└── SKILL.md
```

---

## 五、完整执行时序（v5.0）

```
python task-runner.py queue start
    ↓
主进程启动
  → 启动 Watchdog 守护线程（立即扫描一次孤儿任务）
  → 启动 Notifier 发送线程
    ↓
取出第一个 pending 任务
    ↓
资源预检（内存/磁盘）
    ↓
status = running，写心跳文件
    ↓
StuckAwareRunner.run_with_retry()
    ↓
  ┌──────────────────────────────────────────────┐
  │  Popen 启动 skill 子进程                      │
  │  读取线程：有输出 → 刷新 last_output_time       │
  │  主循环每秒检查：                              │
  │    正常结束 → 返回结果                         │
  │    卡住（10min无输出） → kill → 等待 → 重试     │
  │    超总时限（90min） → kill → 重试             │
  │    重试耗尽 → status=stuck → 跳过             │
  └──────────────────────────────────────────────┘
    ↓
status = completed / error / stuck
写 checkpoint，GC，等待任务间隔
    ↓
取下一个任务……
    ↓
全部完成 → 汇总通知

【同时，后台每 10 分钟：】
Watchdog 扫描 running 状态任务
  → 心跳超时 15min → 强制恢复 → 重新入队
```

---

## 六、改造清单（从 v4.0 升级到 v5.0）

| 文件 | 改动内容 | 优先级 |
|------|----------|--------|
| `stuck_runner.py` | 全新文件，实现 StuckAwareRunner | P0 |
| `task-runner.py` | `call_skill()` 改用 StuckAwareRunner | P0 |
| `watchdog.py` | 全新文件，实现看门狗线程 | P0 |
| `task-runner.py` | 启动时拉起 Watchdog + Notifier 线程 | P0 |
| `resource_guard.py` | 跨平台内存检测 + 预检逻辑 | P1 |
| `notifier.py` | 全新文件，异步通知队列 | P1 |
| `task-runner.py` | tasks.json 新增 retry_count 等字段 | P1 |
| `SKILL.md` | 更新文档，说明卡住检测机制 | P2 |

---

## 七、关键设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| 卡住检测方式 | 输出无进展超时（非进程存活超时） | openclaw 流式进程不会自然退出 |
| 重试粒度 | 单任务级别重试 | 不影响其他任务，最小化重试成本 |
| Watchdog 触发阈值 | 15 分钟无心跳 | 留出正常无输出阶段（如模型思考），避免误判 |
| 任务串行 | 严格串行，不并发 | 2GB 内存无法支撑多个 skill 并发 |
| 通知方式 | 异步队列 | 网络不稳定时不阻塞任务执行 |
| 内存检测 | psutil 优先，多级 fallback | 服务器环境不确定，不能假设 Linux |

---

*版本：v5.0 | 日期：2026-03-15 | 状态：设计确认，待实现*
