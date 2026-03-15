#!/usr/bin/env python3.10
"""
测试师模块 - 双模型商量最终验收
qwen3.5-plus ←→ claude-sonnet-4.6 协商，生成 Report.md

参考：dual-expert-chat 降级策略
"""

import json
import subprocess
from datetime import datetime
from pathlib import Path

WORKSPACE = Path("/home/admin/.openclaw/workspace")
REPORTS_DIR = WORKSPACE / "reports"
CHECKPOINT_DIR = WORKSPACE / ".checkpoints"
REPORTS_DIR.mkdir(exist_ok=True)

# 模型配置
MODEL_QWEN = "dashscope-coding/qwen3.5-plus"
MODEL_CLAUDE = "openai/claude-sonnet-4-6"


def call_model(prompt: str, model: str, label: str = "专家") -> str:
    """调用模型 - 支持降级策略"""
    try:
        result = subprocess.run(
            ["openclaw", "send", "--model", model, prompt],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=300  # 5 分钟超时
        )
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            print(f"⚠️ openclaw 命令失败：{result.stderr}")
            return f"[{model} 调用失败]"
    except subprocess.TimeoutExpired:
        return f"[{model} 超时]"
    except Exception as e:
        return f"[{model} 错误：{str(e)}]"


def generate_report(task_name: str, session_key: str = None) -> dict:
    """
    双模型商量生成最终报告
    
    流程（参考 dual-expert-chat 辩论协作模式）：
    1. qwen3.5-plus 汇总所有步骤结果
    2. claude-sonnet-4.6 审查 + 验收意见
    3. qwen3.5-plus 回应
    4. claude-sonnet-4.6 最终认可
    
    返回：Report.md 内容
    """
    # 加载所有检查点
    checkpoints = load_all_checkpoints()
    
    report = {
        "task_name": task_name,
        "created_at": datetime.now().isoformat(),
        "total_steps": len(checkpoints),
        "completed_steps": 0,
        "failed_steps": 0,
        "status": "draft"
    }
    
    # 统计完成情况
    for cp in checkpoints:
        if cp.get("verifier_decision") == "done":
            report["completed_steps"] += 1
        else:
            report["failed_steps"] += 1
    
    print(f"✅ 测试师阶段启动：{task_name}")
    print(f"📊 统计：{report['completed_steps']}/{report['total_steps']} 完成")
    
    # Round 1: qwen 汇总
    print("📝 Round 1: qwen3.5-plus 汇总...")
    qwen_summary = call_model(
        f"""任务 "{task_name}" 执行完成，请汇总结果：

**总步骤数：** {report['total_steps']}
**完成：** {report['completed_steps']}
**失败：** {report['failed_steps']}

**详细检查点：**
```json
{json.dumps(checkpoints, ensure_ascii=False, indent=2)}
```

请总结：
1. 任务整体是否成功？
2. 哪些步骤完成得好？
3. 哪些步骤有问题？""",
        MODEL_QWEN,
        "汇总者 (Qwen)"
    )
    
    # Round 2: claude 审查
    print("🔍 Round 2: claude-sonnet-4.6 审查...")
    claude_review = call_model(
        f"""审查以下任务总结：

{qwen_summary}

请判断：
1. 任务是否可以标记为"完成"？
2. 是否有需要返工的步骤？
3. 最终验收意见是什么？""",
        MODEL_CLAUDE,
        "验收者 (Claude)"
    )
    
    # Round 3: qwen 回应
    print("📝 Round 3: qwen3.5-plus 回应...")
    qwen_response = call_model(
        f"""claude 的验收意见：

{claude_review}

请回应并生成最终报告。""",
        MODEL_QWEN,
        "汇总者 (Qwen)"
    )
    
    # Round 4: claude 最终认可
    print("✅ Round 4: claude-sonnet-4.6 最终认可...")
    claude_final = call_model(
        f"""最终验收：

{qwen_response}

如认可，回复"✅ 验收通过"并确认报告可以发布。""",
        MODEL_CLAUDE,
        "验收者 (Claude)"
    )
    
    # 确定最终状态
    if report["failed_steps"] == 0:
        report["status"] = "success"
    elif report["failed_steps"] <= report["total_steps"] * 0.2:
        report["status"] = "partial_success"
    else:
        report["status"] = "failed"
    
    report["completed_at"] = datetime.now().isoformat()
    
    # 写入 Report.md
    write_report_md(report, checkpoints, qwen_summary, claude_final)
    
    return report


def load_all_checkpoints() -> list:
    """加载所有检查点"""
    checkpoints = []
    for cp_file in sorted(CHECKPOINT_DIR.glob("step_*_verified.json")):
        with open(cp_file, 'r', encoding='utf-8') as f:
            checkpoints.append(json.load(f))
    return checkpoints


def write_report_md(report: dict, checkpoints: list, qwen_summary: str, claude_final: str):
    """写入 Report.md"""
    report_file = REPORTS_DIR / f"{report['task_name'].replace(' ', '-')}-report.md"
    
    status_emoji = {"success": "✅", "partial_success": "⚠️", "failed": "❌"}
    
    content = f"""# Report.md - {report['task_name']}

**创建时间：** {report['created_at']}
**完成时间：** {report.get('completed_at', 'N/A')}
**状态：** {status_emoji.get(report['status'], '?')} {report['status']}

## 执行统计

| 指标 | 数值 |
|------|------|
| 总步骤数 | {report['total_steps']} |
| 完成步骤 | {report['completed_steps']} |
| 失败步骤 | {report['failed_steps']} |
| 成功率 | {report['completed_steps']/report['total_steps']*100:.1f}% |

## 步骤详情

| Step ID | 步骤名称 | 执行器 | 验证器 | 状态 |
|---------|---------|--------|--------|------|
"""
    
    for cp in checkpoints:
        status = cp.get("verifier_decision", "unknown")
        status_emoji = {"done": "✅", "failed": "❌", "retry": "🔄"}.get(status, "?")
        content += f"| {cp['step_id']} | {cp.get('step_name', 'N/A')} | {cp.get('executor', 'N/A')} | {cp.get('verifier', 'N/A')} | {status_emoji} {status} |\n"
    
    content += f"""
## 执行器汇总 (qwen3.5-plus)

{qwen_summary}

## 验证器意见 (claude-sonnet-4.6)

{claude_final}

---
*由双模型协作生成 (qwen3.5-plus + claude-sonnet-4.6)*
"""
    
    report_file.write_text(content, encoding='utf-8')


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法：python tester.py <任务名>")
        sys.exit(1)
    
    task_name = sys.argv[1]
    print(f"测试师模块：为任务 '{task_name}' 生成报告")
