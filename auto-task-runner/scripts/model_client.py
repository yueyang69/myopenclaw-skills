#!/usr/bin/env python3.10
"""
模型客户端 - 参考 dual-expert-chat 降级策略

降级优先级：
1. sessions_spawn (子代理模式) - 最隔离
2. sessions_send (会话切换) - 轻量
3. openclaw 命令 (命令行) - 保底

内存优化：
- 自动检测可用内存
- 内存 <300MB 时强制使用 openclaw 命令
"""

import subprocess
import json
import os
from datetime import datetime
from pathlib import Path

# 模型配置
MODEL_QWEN = "dashscope-coding/qwen3.5-plus"
MODEL_CLAUDE = "openai/claude-sonnet-4-6"

# 内存阈值 (MB)
MEMORY_THRESHOLD_SPAWN = 500  # <500MB 时禁用 sessions_spawn
MEMORY_THRESHOLD_SEND = 300   # <300MB 时强制使用 openclaw 命令


def get_available_memory_mb() -> int:
    """获取可用内存 (MB)"""
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemAvailable:'):
                    return int(line.split()[1]) / 1024  # KB -> MB
    except:
        pass
    return 1000  # 默认假设 1000MB 可用


def call_model(prompt: str, model: str, label: str = "专家", timeout_seconds: int = 300) -> str:
    """
    调用模型 - 三级降级策略
    
    参考：dual-expert-chat._call_model_with_fallback
    
    降级优先级：
    1. sessions_spawn (子代理) - 内存充足时使用
    2. sessions_send (会话切换) - 中等内存
    3. openclaw 命令 - 内存紧张或前两者失败
    
    参数：
        prompt: 提示词
        model: 模型名称
        label: 角色标签（用于日志）
        timeout_seconds: 超时时间
    
    返回：
        模型响应文本
    """
    available_mem = get_available_memory_mb()
    log(f"🧠 {label} ({model}) - 可用内存：{available_mem:.0f}MB")
    
    # 根据内存选择模式
    if available_mem < MEMORY_THRESHOLD_SEND:
        log(f"⚠️ 内存紧张，使用 openclaw 命令模式")
        return _call_via_openclaw_cmd(prompt, model, label, timeout_seconds)
    elif available_mem < MEMORY_THRESHOLD_SPAWN:
        log(f"ℹ️ 使用 sessions_send 模式")
        return _call_via_sessions_send(prompt, model, label, timeout_seconds)
    else:
        log(f"✅ 使用 sessions_spawn 模式")
        return _call_via_sessions_spawn(prompt, model, label, timeout_seconds)


def _call_via_sessions_spawn(prompt: str, model: str, label: str, timeout: int) -> str:
    """模式 1: sessions_spawn (子代理)"""
    try:
        from openclaw.tools.sessions_spawn import sessions_spawn
        
        result = sessions_spawn(
            mode="run",
            runtime="subagent",
            model=model,
            task=f"{prompt}\n\n请直接输出回答，不要解释工具调用。",
            label=f"task-{label}",
            runTimeoutSeconds=timeout,
            cleanup="delete"  # 关键：自动清理，省内存
        )
        log(f"✅ {label} - sessions_spawn 成功")
        return result.get("result", "[无响应]")
    except Exception as e:
        log(f"⚠️ {label} - sessions_spawn 失败：{e}")
        return _call_via_sessions_send(prompt, model, label, timeout)


def _call_via_sessions_send(prompt: str, model: str, label: str, timeout: int) -> str:
    """模式 2: sessions_send (会话切换)"""
    try:
        from openclaw.tools.sessions_send import sessions_send
        
        result = sessions_send(
            message=f"{prompt}\n\n请直接输出回答。",
            model=model,
            timeoutSeconds=timeout
        )
        log(f"✅ {label} - sessions_send 成功")
        return result if isinstance(result, str) else str(result)
    except Exception as e:
        log(f"⚠️ {label} - sessions_send 失败：{e}")
        return _call_via_openclaw_cmd(prompt, model, label, timeout)


def _call_via_openclaw_cmd(prompt: str, model: str, label: str, timeout: int) -> str:
    """模式 3: openclaw 命令 (保底)"""
    try:
        # 写入临时文件，避免命令行长度限制
        temp_file = Path("/tmp/auto-task-prompt.txt")
        temp_file.write_text(prompt, encoding='utf-8')
        
        result = subprocess.run(
            ["openclaw", "send", "--model", model, f"@{temp_file}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=timeout
        )
        
        if result.returncode == 0:
            log(f"✅ {label} - openclaw 命令成功")
            return result.stdout.strip()
        else:
            log(f"⚠️ {label} - openclaw 命令失败：{result.stderr[:200]}")
            return f"[{model} 调用失败]"
    except subprocess.TimeoutExpired:
        log(f"❌ {label} - 超时 ({timeout}s)")
        return f"[{model} 超时]"
    except Exception as e:
        log(f"❌ {label} - 错误：{str(e)[:200]}")
        return f"[{model} 错误]"


def log(message: str):
    """记录日志"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] {message}\n"
    print(log_line.strip())
    
    # 追加到日志文件
    log_dir = Path("/home/admin/.openclaw/workspace/logs")
    log_dir.mkdir(exist_ok=True)
    with open(log_dir / "model-client.log", 'a', encoding='utf-8') as f:
        f.write(log_line)


if __name__ == "__main__":
    # 测试
    import sys
    if len(sys.argv) < 2:
        print("用法：python model_client.py <提示词>")
        sys.exit(1)
    
    prompt = " ".join(sys.argv[1:])
    response = call_model(prompt, MODEL_QWEN, "测试")
    print(f"\n响应：{response[:500]}")
