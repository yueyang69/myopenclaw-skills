# auto-task-runner 已知问题与修复计划

**创建日期：** 2026-03-15  
**来源：** 外部模型代码审查  
**验证状态：** ✅ 已验证

---

## 🔴 高优先级

### 1. 竞态条件 — tasks.json 无文件锁

**问题描述：**  
`task-runner.py` 和 `watchdog.py` 同时读写 `tasks.json`，无文件锁保护。多进程/线程并发时可能导致数据损坏。

**影响范围：**  
- `_load_tasks()` / `_save_tasks()` 在两个文件中都被调用
- Watchdog 线程与主任务队列同时运行时可能冲突

**修复方案：**  
使用 `fcntl`（Linux）或 `filelock` 库（跨平台）添加文件锁。

**状态：** ⏳ 待修复

---

## 🟡 中优先级

### 2. deferred 任务永久丢失

**问题描述：**  
`run_queue()` 中过滤 pending 任务时未包含 `deferred` 状态：
```python
pending = [t for t in tasks if t["status"] in ("pending", "running", "retrying")]
```
导致内存不足延后的任务永远不会被执行。

**修复方案：**  
```python
pending = [t for t in tasks if t["status"] in ("pending", "running", "retrying", "deferred")]
```

**状态：** ⏳ 待修复

### 3. Windows SIGTERM 不支持

**问题描述：**  
`watchdog.py` 的 `_kill_pid()` 使用 `signal.SIGTERM`，Windows 不支持此信号。

**修复方案：**  
Windows 改用 `signal.SIGBREAK`，或统一使用 `proc.kill()`（跨平台）。

**状态：** ⏳ 待修复

### 4. 锁内磁盘 IO

**问题描述：**  
Notifier 和 Watchdog 的 `_save_queue()` / `_save_tasks()` 在持有锁的情况下写磁盘，可能阻塞其他线程。

**修复方案：**  
将写操作移到锁外，或采用 copy-on-write 模式。

**状态：** ⏳ 待修复（风险可控，暂不紧急）

---

## 🟢 低优先级

### 5. 僵尸进程

**问题描述：**  
`stuck_runner.py` 的 `proc.kill()` 后没有 `proc.wait()`，可能留下僵尸进程。

**修复方案：**  
`proc.kill()` 后添加 `proc.wait(timeout=5)`。

**状态：** ⏳ 待修复

### 6. 未用变量

**问题描述：**  
`task-runner.py` 第 20 行 `STEP_INTERVAL_SECONDS` 定义但从未使用。

**修复方案：**  
删除该变量。

**状态：** ⏳ 待修复

### 7. 闭包双改（边缘）

**问题描述：**  
`on_stuck` 回调内直接修改 `tasks` 列表，但由外层 `call_skill` 传入，风险可控。

**状态：** ✅ 可接受（无需修复）

### 8. 多任务快照覆盖（理论）

**问题描述：**  
`_recover_task` 前未重新 `_load_tasks()`，但 Watchdog 独享线程，冲突概率低。

**状态：** ✅ 可接受（无需修复）

### 9. 通知漏发（边缘）

**问题描述：**  
critical 通知在 `pre_check()` 内已发送，但进程可能立即退出导致队列未 flush。

**状态：** ✅ 可接受（Notifier 有 stop() 前的 flush 机制）

### 10. 通知丢失（理论）

**问题描述：**  
`_flush()` 已有锁保护，但加载 - 处理 - 保存流程较长。

**状态：** ✅ 可接受（已有锁保护）

---

## 修复计划

| 优先级 | 问题 | 预计工时 | 依赖 |
|--------|------|----------|------|
| 🔴 高 | 竞态条件 | 2h | filelock 库 |
| 🟡 中 | deferred 丢失 | 0.5h | 无 |
| 🟡 中 | Windows SIGTERM | 0.5h | 无 |
| 🟢 低 | 僵尸进程 | 0.5h | 无 |
| 🟢 低 | 未用变量 | 0.1h | 无 |

---

## 备注

- 大部分问题影响概率低，但竞态条件和 deferred 丢失需优先修复
- Windows 兼容性问题仅在跨平台部署时需要考虑
- 通知系统已有基本保护机制，风险可控
