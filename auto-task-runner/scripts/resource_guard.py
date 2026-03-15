#!/usr/bin/env python3
"""
resource_guard.py - 资源守卫（v5.0 升级版）

改动：
  - get_available_memory_mb()：psutil → Linux → macOS → Windows 四级降级，修复 Windows/macOS 返回 999 的问题
  - 新增 pre_check()：任务启动前资源预检，内存不足时等待或跳过
  - 模型限流逻辑保持不变
"""

import gc
import json
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

RUNTIME_DIR      = Path(__file__).parent.parent / ".runtime"
MODEL_USAGE_FILE = RUNTIME_DIR / "model_usage.json"

# 内存阈值
MEMORY_WARN_MB     = 300
MEMORY_CRITICAL_MB = 150

# 磁盘阈值
RUNTIME_SIZE_LIMIT_MB = 2000

# 模型限流
QWEN_LIMIT_PER_HOUR   = 40
CLAUDE_LIMIT_PER_HOUR = 15

# 预检重试
PRE_CHECK_WAIT_SECS  = 60   # 每次等待时间
PRE_CHECK_MAX_TRIES  = 3    # 最多等待次数


# ===========================================================================
# 内存检测（四级降级，跨平台）
# ===========================================================================

def get_available_memory_mb() -> float:
    """
    获取系统可用内存（MB）。

    优先级：
      1. psutil（跨平台最优）
      2. Linux  /proc/meminfo
      3. macOS  vm_stat
      4. Windows wmic
      5. 全部失败 → 999（跳过检查）
    """
    # 1. psutil
    try:
        import psutil
        return psutil.virtual_memory().available / 1024 / 1024
    except ImportError:
        pass
    except Exception:
        pass

    # 2. Linux
    try:
        with open('/proc/meminfo', encoding='utf-8') as f:
            for line in f:
                if line.startswith('MemAvailable:'):
                    return int(line.split()[1]) / 1024
    except Exception:
        pass

    # 3. macOS
    try:
        out = subprocess.check_output(['vm_stat'], text=True, timeout=5)
        page_size = 4096
        free_pages = 0
        for line in out.splitlines():
            if 'Pages free' in line:
                free_pages += int(line.split(':')[1].strip().rstrip('.'))
            elif 'Pages inactive' in line:
                free_pages += int(line.split(':')[1].strip().rstrip('.'))
        if free_pages > 0:
            return free_pages * page_size / 1024 / 1024
    except Exception:
        pass

    # 4. Windows
    try:
        out = subprocess.check_output(
            ['wmic', 'OS', 'get', 'FreePhysicalMemory', '/Value'],
            text=True, timeout=5
        )
        for line in out.splitlines():
            if 'FreePhysicalMemory' in line:
                kb = int(line.split('=')[1].strip())
                return kb / 1024
    except Exception:
        pass

    # 5. 全部失败
    print("⚠️  WARNING: 无法检测可用内存，跳过内存检查")
    return 999.0


def get_dir_size_mb(path: Path) -> float:
    """获取目录大小（MB）"""
    total = 0
    try:
        for item in path.rglob('*'):
            if item.is_file():
                total += item.stat().st_size
    except Exception:
        pass
    return total / (1024 * 1024)


# ===========================================================================
# 内存检查
# ===========================================================================

def check_memory() -> bool:
    """
    检查内存，必要时 GC。
    返回 True = 内存充足，False = 内存严重不足（建议暂停）
    """
    mem = get_available_memory_mb()

    if mem < MEMORY_CRITICAL_MB:
        print(f"❌ 内存严重不足：{mem:.0f}MB < {MEMORY_CRITICAL_MB}MB，执行 GC")
        gc.collect()
        mem = get_available_memory_mb()
        if mem < MEMORY_CRITICAL_MB:
            print(f"❌ GC 后内存仍不足：{mem:.0f}MB，建议暂停任务")
            return False
    elif mem < MEMORY_WARN_MB:
        print(f"⚠️  内存紧张：{mem:.0f}MB < {MEMORY_WARN_MB}MB，执行 GC")
        gc.collect()
    else:
        print(f"✅ 内存充足：{mem:.0f}MB")

    return True


def pre_check(task_id: int, notifier=None, log_fn=None) -> str:
    """
    任务启动前资源预检。

    返回值：
      "ok"       - 资源充足，可以执行
      "deferred" - 内存持续不足，任务应延后（跳过本次）
      "critical" - 内存极度不足，应退出进程由 pm2 重启
    """
    log = log_fn or print

    for attempt in range(PRE_CHECK_MAX_TRIES):
        mem = get_available_memory_mb()

        if mem < MEMORY_CRITICAL_MB:
            log(f"❌ 内存极度不足 {mem:.0f}MB，触发主动退出")
            if notifier:
                try:
                    notifier.memory_low(task_id, mem)
                except Exception:
                    pass
            return "critical"

        if mem >= MEMORY_WARN_MB:
            return "ok"

        # 内存在警告区间：等待后重试
        log(f"⚠️  内存不足 {mem:.0f}MB，等待 {PRE_CHECK_WAIT_SECS}s 后重试（{attempt+1}/{PRE_CHECK_MAX_TRIES}）")
        if notifier and attempt == 0:
            try:
                notifier.memory_low(task_id, mem)
            except Exception:
                pass
        gc.collect()
        time.sleep(PRE_CHECK_WAIT_SECS)

    # 超过最大等待次数
    mem = get_available_memory_mb()
    if mem < MEMORY_WARN_MB:
        log(f"⚠️  内存持续不足 {mem:.0f}MB，任务 [{task_id}] 延后")
        return "deferred"
    return "ok"


# ===========================================================================
# 磁盘检查
# ===========================================================================

def check_disk(target_dir: Path, log_fn=None):
    """检查磁盘，超限时清理旧文件"""
    log = log_fn or print
    size = get_dir_size_mb(target_dir)

    if size > RUNTIME_SIZE_LIMIT_MB:
        log(f"⚠️  磁盘超限：{size:.0f}MB > {RUNTIME_SIZE_LIMIT_MB}MB，清理旧文件")
        _cleanup_old_files(target_dir, log)
    else:
        log(f"✅ 磁盘充足：{size:.0f}MB / {RUNTIME_SIZE_LIMIT_MB}MB")


def _cleanup_old_files(target_dir: Path, log_fn=None):
    """清理旧 checkpoint（保留最近 100 个）"""
    log = log_fn or print
    checkpoint_dir = target_dir / "checkpoints"
    if not checkpoint_dir.exists():
        return

    files = sorted(checkpoint_dir.glob("*.json"), key=lambda x: x.stat().st_mtime)
    if len(files) > 100:
        to_delete = files[:-100]
        for f in to_delete:
            try:
                f.unlink()
                log(f"   删除旧 checkpoint：{f.name}")
            except Exception as e:
                log(f"   删除失败：{e}")


# ===========================================================================
# 模型调用次数监控
# ===========================================================================

def _init_model_usage() -> dict:
    RUNTIME_DIR.mkdir(exist_ok=True, parents=True)
    return {
        "window_start": datetime.now().isoformat(),
        "window_minutes": 60,
        "qwen":   {"count": 0, "limit": QWEN_LIMIT_PER_HOUR,   "status": "ok"},
        "claude": {"count": 0, "limit": CLAUDE_LIMIT_PER_HOUR, "status": "ok"}
    }


def _load_model_usage() -> dict:
    if MODEL_USAGE_FILE.exists():
        try:
            data = json.loads(MODEL_USAGE_FILE.read_text(encoding="utf-8"))
            window_start = datetime.fromisoformat(data.get("window_start", ""))
            if datetime.now() - window_start > timedelta(minutes=data.get("window_minutes", 60)):
                return _init_model_usage()   # 窗口过期，重置
            return data
        except Exception:
            pass
    return _init_model_usage()


def _save_model_usage(data: dict):
    MODEL_USAGE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def check_model_call(model_name: str, log_fn=None) -> bool:
    """
    检查模型调用是否被允许。
    返回 True = 允许，False = 被限流。
    """
    log = log_fn or print
    data = _load_model_usage()

    key = model_name.lower()
    if key not in data:
        return True   # 未知模型，放行

    model_data = data[key]
    if model_data["count"] >= model_data["limit"]:
        log(f"⚠️  {model_name} 已达限流：{model_data['count']}/{model_data['limit']} 次/小时")
        return False

    model_data["count"] += 1
    model_data["status"] = "ok"
    _save_model_usage(data)
    log(f"✅ {model_name} 调用计数：{model_data['count']}/{model_data['limit']}")
    return True


def report_model_usage(log_fn=None):
    log = log_fn or print
    data = _load_model_usage()
    log(f"模型调用统计（窗口：{data['window_start']}）")
    log(f"  qwen:   {data['qwen']['count']}/{data['qwen']['limit']} 次")
    log(f"  claude: {data['claude']['count']}/{data['claude']['limit']} 次")


# ===========================================================================
# 独立测试
# ===========================================================================
if __name__ == "__main__":
    print("=== 内存检测 ===")
    mem = get_available_memory_mb()
    print(f"可用内存：{mem:.1f} MB")

    print("\n=== 内存检查 ===")
    check_memory()

    print("\n=== 磁盘检查 ===")
    check_disk(RUNTIME_DIR)

    print("\n=== 模型限流 ===")
    for i in range(3):
        allowed = check_model_call("qwen")
        print(f"  第{i+1}次 qwen 调用：{'允许' if allowed else '限流'}")

    report_model_usage()
