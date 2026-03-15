#!/usr/bin/env python3
"""
Deep Learning Guide - 深度学习指导技能
双模型协作版：Qwen 规划 + Claude 审核
"""

import sys
import json
from pathlib import Path
from datetime import datetime

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "auto-task-runner" / "scripts"))

try:
    from model_client import call_model, MODEL_QWEN, MODEL_CLAUDE
except ImportError:
    MODEL_QWEN = "dashscope-coding/qwen3.5-plus"
    MODEL_CLAUDE = "openai/claude-sonnet-4-6"
    def call_model(prompt, model, label="专家", timeout_seconds=300):
        return f"[模拟响应：{label}]"

WORKSPACE = Path(__file__).parent.parent
TEMPLATES_DIR = Path(__file__).parent / "templates"


def deep_learn(topic: str):
    """启动深度学习计划 - 双模型协作"""
    print(f"🎯 启动深度学习：{topic}")
    
    # Step 1: Qwen 生成知识地图
    print("\n📍 Step 1: 生成知识地图...")
    map_prompt = f"""
为"{topic}"生成一个精简知识地图，包含：
1. 核心概念（5-8 个，必须掌握）
2. 学习路径（分 3 阶段：入门→进阶→精通）
3. 推荐资源（每个阶段 2-3 个，优先免费）
4. 里程碑（3 个可验证的输出）

格式：Markdown，简洁，不要废话。
"""
    knowledge_map = call_model(map_prompt, MODEL_QWEN, "Architect-Qwen")
    print(knowledge_map[:500] + "..." if len(knowledge_map) > 500 else knowledge_map)
    
    # Step 2: Claude 审核
    print("\n🔍 Step 2: 审核知识地图...")
    review_prompt = f"""
审核以下知识地图，指出：
1. 是否遗漏核心概念？
2. 学习路径是否合理？
3. 资源是否过时？

知识地图：
{knowledge_map[:2000]}

格式：简洁列出问题（如有），没问题就说"通过"。
"""
    review = call_model(review_prompt, MODEL_CLAUDE, "Verifier-Claude")
    print(review)
    
    # Step 3: 生成 30 天计划
    print("\n📅 Step 3: 生成 30 天学习计划...")
    plan_prompt = f"""
基于以上知识地图，生成 30 天学习计划：
- 每天 1-2 小时
- 每周 1 个里程碑
- 包含：学习内容 + 输出任务 + 复习时间

格式：表格（周次 | 主题 | 输出 | 复习）
"""
    plan = call_model(plan_prompt, MODEL_QWEN, "Planner-Qwen")
    print(plan[:500] + "..." if len(plan) > 500 else plan)
    
    # Step 4: 保存
    print("\n💾 Step 4: 保存学习材料...")
    save_learning_materials(topic, knowledge_map, plan)
    
    print("\n✅ 完成！学习材料已保存到：")
    print(f"   {WORKSPACE}/skills/deep-learning-guide/sessions/{topic}/")


def save_learning_materials(topic: str, knowledge_map: str, plan: str):
    """保存学习材料"""
    session_dir = WORKSPACE / "skills" / "deep-learning-guide" / "sessions" / topic
    session_dir.mkdir(parents=True, exist_ok=True)
    
    # 知识地图
    with open(session_dir / "knowledge-map.md", 'w', encoding='utf-8') as f:
        f.write(f"# {topic} - 知识地图\n\n")
        f.write(f"生成时间：{datetime.now().isoformat()}\n\n")
        f.write(knowledge_map)
    
    # 学习计划
    with open(session_dir / "study-plan.md", 'w', encoding='utf-8') as f:
        f.write(f"# {topic} - 30 天学习计划\n\n")
        f.write(f"生成时间：{datetime.now().isoformat()}\n\n")
        f.write(plan)
    
    # 学习日志模板
    template = """# 学习日志

## [日期]

**学习时长：** [ ] 小时

**今天学了什么：**


**核心收获（1 句话总结）：**


**卡点/问题：**


**明日计划：**

"""
    with open(session_dir / "learning-log.md", 'w', encoding='utf-8') as f:
        f.write(template)


def learn_status(topic: str = None):
    """查看学习进度"""
    sessions_dir = WORKSPACE / "skills" / "deep-learning-guide" / "sessions"
    
    if not sessions_dir.exists():
        print("📭 暂无学习记录")
        return
    
    if topic:
        session_dir = sessions_dir / topic
        if session_dir.exists():
            print(f"📊 {topic} - 学习进度")
            files = list(session_dir.glob("*.md"))
            print(f"   文件：{[f.name for f in files]}")
        else:
            print(f"❌ 未找到主题：{topic}")
    else:
        print("📚 学习记录：")
        for d in sessions_dir.iterdir():
            if d.is_dir():
                print(f"   - {d.name}")


def learn_review(topic: str):
    """复习已学内容"""
    session_dir = WORKSPACE / "skills" / "deep-learning-guide" / "sessions" / topic
    
    if not session_dir.exists():
        print(f"❌ 未找到主题：{topic}")
        return
    
    # 生成复习题
    print(f"🔄 生成 {topic} 复习题...")
    review_prompt = f"""
为"{topic}"生成 5 道复习题：
- 2 道基础概念题
- 2 道应用题
- 1 道综合题

格式：
## 基础题
1. 问题？
   <details><summary>答案</summary>答案内容</details>
"""
    questions = call_model(review_prompt, MODEL_QWEN, "Reviewer-Qwen")
    print(questions)
    
    # 保存复习题
    review_file = session_dir / "review-questions.md"
    with open(review_file, 'w', encoding='utf-8') as f:
        f.write(f"# {topic} - 复习题\n\n")
        f.write(f"生成时间：{datetime.now().isoformat()}\n\n")
        f.write(questions)
    
    print(f"\n💾 复习题已保存：{review_file}")


def main():
    if len(sys.argv) < 2:
        print("用法：python3 main.py <命令> [参数]")
        print("\n命令：")
        print("  deep_learn [主题]     - 启动深度学习计划")
        print("  learn_status [主题]   - 查看学习进度")
        print("  learn_review [主题]   - 生成复习题")
        print("\n示例：")
        print("  python3 main.py deep_learn Rust")
        print("  python3 main.py learn_status Rust")
        print("  python3 main.py learn_review Rust")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "deep_learn":
        if len(sys.argv) < 3:
            print("❌ 请指定学习主题")
            print("用法：python3 main.py deep_learn [主题]")
            sys.exit(1)
        topic = " ".join(sys.argv[2:])
        deep_learn(topic)
    
    elif command == "learn_status":
        topic = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else None
        learn_status(topic)
    
    elif command == "learn_review":
        if len(sys.argv) < 3:
            print("❌ 请指定复习主题")
            sys.exit(1)
        topic = " ".join(sys.argv[2:])
        learn_review(topic)
    
    else:
        print(f"❌ 未知命令：{command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
