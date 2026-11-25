import json
import httpx
from typing import List, Dict, Any, Optional
from backend.config import settings

GRADE6_ARITHMETIC_PROMPT = """你是一名专注小学六年级数学“保及格”（40-58分临界生）提分专家。
核心教学与解题规范红线：
1. 【小学算术红线】：所有解题步骤与变式题必须使用小学六年级算术方法（或简易一元一次方程），严禁使用二元一次方程组、负数列式、二次方程等初中解法！
2. 【保及格难度约束】：严禁拔高出奥数题或复杂组合压轴题！变式题仅更换生活场景与友好数字（约简后分母不超过 50，圆周率默认 π=3.14）。
3. 【隐私保护红线】：严禁在回复中包含任何学生真实姓名，所有学情输出仅基于匿名编号。
"""

async def _call_deepseek_json(messages: List[Dict[str, str]], temperature: float = 0.3) -> Dict[str, Any]:
    url = f"{settings.DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": settings.DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"}
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)

async def classify_mistake(
    question_text: str,
    student_work: str,
    kb_candidates: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Classifies student mistake, matches mother question, extracts error tags and 3-min face-to-face script.
    """
    if settings.MOCK_EXTERNAL_APIS or not settings.DEEPSEEK_API_KEY or "sk-mock" in settings.DEEPSEEK_API_KEY:
        # High quality offline fallback
        mother = kb_candidates[0] if kb_candidates else {"id": "G6-CALC-01", "module_tag": "calc"}
        mid = mother.get("id", "G6-CALC-01")
        if "FRAC" in mid or "工程" in question_text or "天数" in question_text:
            return {
                "kb_mother_id": "G6-FRAC-01",
                "module_tag": "fraction",
                "error_tags": ["找错单位1", "乘除混淆"],
                "face_to_face_script": "题目中没有告诉具体工程总量，我们把这项工程总量看作几？那甲每天做几分之几？求合作天数用总工作量除以什么？"
            }
        elif "RATIO" in mid or "比" in question_text or "长方形" in question_text:
            return {
                "kb_mother_id": "G6-RATIO-01",
                "module_tag": "ratio",
                "error_tags": ["周长未除以2", "比前后项颠倒"],
                "face_to_face_script": "长方形的周长包含两个长和两个宽，那么一个长加一个宽等于多少？长和宽的比是几比几，怎么求出各占几分之几？"
            }
        elif "GEO" in mid or "圆" in question_text or "半径" in question_text:
            return {
                "kb_mother_id": "G6-GEO-01",
                "module_tag": "geometry",
                "error_tags": ["平方算成乘2", "混淆周长与面积"],
                "face_to_face_script": "圆的面积公式是 S=π*r²，这里的平方是半径乘半径，还是半径乘2？先算出 r² 再乘 3.14！"
            }
        else:
            return {
                "kb_mother_id": "G6-CALC-01",
                "module_tag": "calc",
                "error_tags": ["分配律漏乘", "计算粗心"],
                "face_to_face_script": "括号外面乘一个数，括号里面的每一项都要乘到。你看看第二项有没有乘漏？"
            }

    system_prompt = GRADE6_ARITHMETIC_PROMPT + """
任务：对学生手写错题进行错因归类并给出3分钟面批反问话术。
输出必须为严格的JSON对象，包含：
- "kb_mother_id": 匹配的母题ID
- "module_tag": "calc" | "fraction" | "ratio" | "geometry"
- "error_tags": 错因标签列表（如 ["乘法分配律漏乘", "找错单位1"]）
- "face_to_face_script": 2句引导反问启发式面批话术
"""
    user_prompt = f"""
错题题干：{question_text}
学生作答：{student_work}
候选母题：{json.dumps([{'id': c['id'], 'title': c['title']} for c in kb_candidates], ensure_ascii=False)}
"""
    try:
        res = await _call_deepseek_json([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ])
        return res
    except Exception as e:
        print(f"DeepSeek classify_mistake error: {e}, using mock fallback.")
        return await classify_mistake(question_text, student_work, kb_candidates)

async def generate_variants(mother_doc: Dict[str, Any], error_tags: List[str], count: int = 2) -> List[Dict[str, Any]]:
    """
    Generates same-source baseline variant questions with pure Python arithmetic verification code.
    """
    mid = mother_doc.get("id", "G6-CALC-01")
    mod = mother_doc.get("module_tag", "calc")

    if settings.MOCK_EXTERNAL_APIS or not settings.DEEPSEEK_API_KEY or "sk-mock" in settings.DEEPSEEK_API_KEY:
        if mod == "fraction" or "FRAC" in mid:
            return [
                {
                    "question_md": "修一条水渠，甲队单独修要 10 天完成，乙队单独修要 15 天完成。两队合修需要几天完成？",
                    "answer_steps_md": "工作总量视为单位“1”：\\n$1 \\div (\\frac{1}{10} + \\frac{1}{15}) = 1 \\div \\frac{5}{30} = 6$（天）。\\n答：两队合修需要 6 天完成。",
                    "expected_answer": "6",
                    "verify_code": "from fractions import Fraction\nres = 1 / (Fraction(1, 10) + Fraction(1, 15))\nprint(int(res))"
                },
                {
                    "question_md": "学校图书角有连环画 80 本，科技书的本数是连环画的 $\\frac{3}{4}$，故事书的本数是科技书的 $\\frac{2}{3}$。故事书有多少本？",
                    "answer_steps_md": "$80 \\times \\frac{3}{4} \\times \\frac{2}{3} = 40$（本）。\\n答：故事书有 40 本。",
                    "expected_answer": "40",
                    "verify_code": "from fractions import Fraction\nres = 80 * Fraction(3, 4) * Fraction(2, 3)\nprint(int(res))"
                }
            ][:count]
        elif mod == "ratio" or "RATIO" in mid:
            return [
                {
                    "question_md": "用 40 厘米长的铁丝围成一个长方形，长和宽的比是 $3:2$。这个长方形的面积是多少平方厘米？",
                    "answer_steps_md": "长宽和：$40 \\div 2 = 20$（厘米）；\\n长：$20 \\times \\frac{3}{5} = 12$（厘米）；\\n宽：$20 \\times \\frac{2}{5} = 8$（厘米）；\\n面积：$12 \\times 8 = 96$（平方厘米）。\\n答：面积是 96 平方厘米。",
                    "expected_answer": "96",
                    "verify_code": "from fractions import Fraction\nl = 20 * Fraction(3, 5)\nw = 20 * Fraction(2, 5)\nprint(int(l * w))"
                },
                {
                    "question_md": "六年级一班和二班共植树 90 棵，两班植树棵数的比是 $4:5$。一班和二班各植树多少棵？",
                    "answer_steps_md": "总份数：$4 + 5 = 9$；\\n一班：$90 \\times \\frac{4}{9} = 40$（棵）；\\n二班：$90 \\times \\frac{5}{9} = 50$（棵）。\\n答：一班植树 40 棵，二班植树 50 棵。",
                    "expected_answer": "40",
                    "verify_code": "from fractions import Fraction\nres = 90 * Fraction(4, 9)\nprint(int(res))"
                }
            ][:count]
        elif mod == "geometry" or "GEO" in mid:
            return [
                {
                    "question_md": "一个圆形花坛的周长是 18.84 米。它的占地面积是多少平方米？（$\\pi = 3.14$）",
                    "answer_steps_md": "半径：$r = 18.84 \\div 3.14 \\div 2 = 3$（米）；\\n面积：$3.14 \\times 3^2 = 3.14 \\times 9 = 28.26$（平方米）。\\n答：占地面积是 28.26 平方米。",
                    "expected_answer": "28.26",
                    "verify_code": "from decimal import Decimal\nr = Decimal('18.84') / Decimal('3.14') / 2\narea = Decimal('3.14') * (r * r)\nprint(area)"
                },
                {
                    "question_md": "在一个半径为 4 米的圆形水池周围修一条宽 1 米的环形便道，便道的面积是多少平方米？（$\\pi = 3.14$）",
                    "answer_steps_md": "外圆半径：$4 + 1 = 5$（米）；\\n便道面积：$3.14 \\times (5^2 - 4^2) = 3.14 \\times 9 = 28.26$（平方米）。\\n答：便道面积是 28.26 平方米。",
                    "expected_answer": "28.26",
                    "verify_code": "from decimal import Decimal\nres = Decimal('3.14') * (5**2 - 4**2)\nprint(res)"
                }
            ][:count]
        else:
            return [
                {
                    "question_md": "计算：$\\frac{5}{8} \\times \\frac{3}{7} + \\frac{5}{8} \\times \\frac{4}{7}$",
                    "answer_steps_md": "原式 $= \\frac{5}{8} \\times (\\frac{3}{7} + \\frac{4}{7}) = \\frac{5}{8} \\times 1 = \\frac{5}{8}$",
                    "expected_answer": "5/8",
                    "verify_code": "from fractions import Fraction\nres = Fraction(5, 8) * Fraction(3, 7) + Fraction(5, 8) * Fraction(4, 7)\nprint(res)"
                },
                {
                    "question_md": "计算：$(\\frac{5}{6} - \\frac{1}{4}) \\times 12$",
                    "answer_steps_md": "原式 $= \\frac{5}{6} \\times 12 - \\frac{1}{4} \\times 12 = 10 - 3 = 7$",
                    "expected_answer": "7",
                    "verify_code": "from fractions import Fraction\nres = (Fraction(5, 6) - Fraction(1, 4)) * 12\nprint(int(res))"
                }
            ][:count]

    system_prompt = GRADE6_ARITHMETIC_PROMPT + """
任务：根据母题和错因，生成小学六年级保底变式题。
输出必须为严格的JSON对象，包含字段 "items"，为一个数组，长度为 """ + str(count) + """：
[
  {
    "question_md": "题干Markdown",
    "answer_steps_md": "小学算术分步解答Markdown",
    "expected_answer": "最终标准答案（数字或最简分数如 3/4 或 28.26）",
    "verify_code": "一段纯Python代码（从fractions导入Fraction或从decimal导入Decimal，严格计算出结果并print(res)）"
  }
]
"""
    user_prompt = f"""
母题信息：
ID: {mid}
模块: {mod}
母题内容: {mother_doc.get('title', '')}
错因标签: {json.dumps(error_tags, ensure_ascii=False)}
请生成 {count} 道变式题。
"""
    try:
        res = await _call_deepseek_json([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ])
        return res.get("items", [])
    except Exception as e:
        print(f"DeepSeek generate_variants error: {e}, using mock fallback.")
        return await generate_variants(mother_doc, error_tags, count)

async def generate_student_report(anonymous_code: str, stats_dict: Dict[str, Any]) -> Dict[str, str]:
    """
    Generates:
    1. 3-line teacher summary
    2. <=80 chars objective & encouraging WeChat weekly report for parents.
    """
    if settings.MOCK_EXTERNAL_APIS or not settings.DEEPSEEK_API_KEY or "sk-mock" in settings.DEEPSEEK_API_KEY:
        return {
            "teacher_summary": (
                f"1. 基础计算掌握扎实，乘法分配律与简算正确率明显提升；\n"
                f"2. 分数应用题找准单位1能力稳步提高，工程问题已基本过关；\n"
                f"3. 下周重点攻关比的按比分配周长折半运算，稳拿及格基本盘。"
            ),
            "wechat_msg": (
                f"【本周数学小结】孩子本周在分数四则简算上进步明显，计算认真且条理清晰；"
                f"针对个别比的按比分配题型已做专项查漏补缺，学习劲头很足，请家长多肯定鼓励！"
            )
        }

    system_prompt = GRADE6_ARITHMETIC_PROMPT + """
任务：根据学生本周学情数据，输出学情总结与家长周报。
输出必须为严格的JSON对象，包含：
- "teacher_summary": 教师端学情诊断（严格3行，指出亮点、薄弱项与下周针对性措施）
- "wechat_msg": 家长微信周报（字数控制在 80 字以内，客观严谨、肯定进步、指出跟进点、温和鼓励）
注意：严禁出现任何学生真实姓名，仅基于学情数据。
"""
    user_prompt = f"学生匿名代码: {anonymous_code}\n学情统计数据: {json.dumps(stats_dict, ensure_ascii=False)}"
    try:
        res = await _call_deepseek_json([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ])
        return {
            "teacher_summary": res.get("teacher_summary", ""),
            "wechat_msg": res.get("wechat_msg", "")
        }
    except Exception as e:
        print(f"DeepSeek generate_student_report error: {e}, using mock fallback.")
        return await generate_student_report(anonymous_code, stats_dict)
