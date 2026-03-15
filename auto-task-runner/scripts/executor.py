#!/usr/bin/env python3.10
"""
执行器模块 - qwen3.5-plus 执行具体命令
"""

import subprocess
import json
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent
CHECKPOINT_DIR = SKILL_DIR / ".runtime" / "checkpoints"
CHECKPOINT_DIR.mkdir(exist_ok=True, parents=True)


def execute_step(step: dict, session_key: str) -> dict:
    """
    执行单个步骤
    
    参数：
        step: {"id": 1, "name": "...", "cmd": "...", ...}
        session_key: 会话 key
    
    返回：
        {
            "step_id": 1,
            "executor": "dashscope-coding/qwen3.5-plus",
            "executor_output": "...",
            "success": true/false,
            "executed_at": "..."
        }
    """
    from sessions_send import sessions_send
    
    result = {
        "step_id": step["id"],
        "step_name": step["name"],
        "executor": "dashscope-coding/qwen3.5-plus",
        "executed_at": datetime.now().isoformat(),
        "success": False,
        "executor_output": ""
    }
    
    # 使用 qwen3.5-plus 执行命令
    response = sessions_send(
        sessionKey=session_key,
        message=f"""执行步骤 {step['id']}: {step['name']}

命令：
```bash
{step['cmd']}
```

请执行并输出完整结果（包括 stdout 和 stderr）。""",
        model="dashscope-coding/qwen3.5-plus"
    )
    
    result["executor_output"] = response
    result["success"] = True  # 假设执行成功，由验证器判断
    
    # 保存执行结果
    save_execution_result(result)
    
    return result


def execute_step_local(step: dict) -> dict:
    """
    本地执行命令（备用方案，不通过模型）
    
    用于简单命令或调试
    """
    result = {
        "step_id": step["id"],
        "step_name": step["name"],
        "executor": "local",
        "executed_at": datetime.now().isoformat(),
        "success": False,
        "executor_output": ""
    }
    
    try:
        cmd_result = subprocess.run(
            step["cmd"],
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=300  # 5 分钟超时
        )
        result["executor_output"] = cmd_result.stdout + cmd_result.stderr
        result["success"] = cmd_result.returncode == 0
    except subprocess.TimeoutExpired:
        result["executor_output"] = "执行超时（>5 分钟）"
    except Exception as e:
        result["executor_output"] = f"执行失败：{str(e)}"
    
    save_execution_result(result)
    return result


def save_execution_result(result: dict):
    """保存执行结果到检查点"""
    checkpoint_file = CHECKPOINT_DIR / f"step_{result['step_id']}_executed.json"
    with open(checkpoint_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法：python executor.py <step_json>")
        sys.exit(1)
    
    step = json.loads(sys.argv[1])
    result = execute_step_local(step)
    print(json.dumps(result, ensure_ascii=False, indent=2))
