import os
import sqlite3
import json
from datetime import datetime
from backend.config import settings

def get_db_path() -> str:
    db_path = settings.SQLITE_DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    return db_path

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # 1. students
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_no TEXT NOT NULL,
        name TEXT NOT NULL,
        anonymous_code TEXT UNIQUE NOT NULL,
        class_name TEXT DEFAULT '六(1)班',
        focus_tier TEXT DEFAULT 'critical',
        baseline_score REAL DEFAULT 50.0,
        current_est_score REAL DEFAULT 50.0,
        l3_summary_override TEXT DEFAULT '',
        created_at TEXT
    );
    """)

    # 2. exam_papers
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS exam_papers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        unit_tag TEXT DEFAULT '',
        exam_date TEXT,
        total_score REAL DEFAULT 100.0,
        created_at TEXT
    );
    """)

    # 3. exam_questions
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS exam_questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id INTEGER NOT NULL REFERENCES exam_papers(id) ON DELETE CASCADE,
        q_no TEXT NOT NULL,
        kb_mother_id TEXT,
        module_tag TEXT,
        max_score REAL DEFAULT 5.0,
        quick_error_tags TEXT DEFAULT '[]'
    );
    """)

    # 4. exam_scores
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS exam_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id INTEGER NOT NULL REFERENCES exam_papers(id) ON DELETE CASCADE,
        student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
        score REAL,
        recorded_at TEXT
    );
    """)

    # 5. mistake_records
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS mistake_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
        source_type TEXT DEFAULT 'exam_tick',
        paper_id INTEGER REFERENCES exam_papers(id) ON DELETE SET NULL,
        exam_question_id INTEGER REFERENCES exam_questions(id) ON DELETE SET NULL,
        kb_mother_id TEXT,
        module_tag TEXT,
        question_snapshot TEXT,
        error_tags TEXT DEFAULT '[]',
        photo_url TEXT DEFAULT '',
        ocr_note TEXT DEFAULT '',
        face_to_face_script TEXT DEFAULT '',
        mastery_status TEXT DEFAULT 'unmastered',
        wrong_count INTEGER DEFAULT 1,
        next_review_date TEXT,
        created_at TEXT,
        updated_at TEXT
    );
    """)

    # 6. variant_pool
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS variant_pool (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kb_mother_id TEXT,
        module_tag TEXT,
        source_label TEXT,
        question_md TEXT NOT NULL,
        image_path TEXT DEFAULT '',
        answer_steps_md TEXT NOT NULL,
        verified INTEGER DEFAULT 1,
        created_at TEXT
    );
    """)

    # 7. consolidation_sheets
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS consolidation_sheets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_code TEXT NOT NULL,
        student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
        title TEXT,
        status TEXT DEFAULT 'generated',
        pdf_path TEXT DEFAULT '',
        face_to_face_script TEXT DEFAULT '',
        parent_wechat_msg TEXT DEFAULT '',
        created_at TEXT,
        checked_at TEXT
    );
    """)

    # 8. sheet_items
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sheet_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sheet_id INTEGER NOT NULL REFERENCES consolidation_sheets(id) ON DELETE CASCADE,
        sort_order INTEGER,
        source_mistake_id INTEGER REFERENCES mistake_records(id) ON DELETE SET NULL,
        kb_mother_id TEXT,
        item_type TEXT,
        question_md TEXT NOT NULL,
        image_path TEXT DEFAULT '',
        answer_steps_md TEXT NOT NULL,
        sandbox_verified INTEGER DEFAULT 1,
        check_result TEXT DEFAULT 'pending'
    );
    """)

    # 9. import_batches (student filtering import, homework batches, DSH OCR batches)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS import_batches (
        id TEXT PRIMARY KEY,
        batch_type TEXT NOT NULL,
        source TEXT DEFAULT 'workbench',
        status TEXT DEFAULT 'pending',
        title TEXT DEFAULT '',
        paper_id INTEGER REFERENCES exam_papers(id) ON DELETE SET NULL,
        raw_ocr_json TEXT DEFAULT '[]',
        preview_plan_json TEXT DEFAULT '{}',
        summary_json TEXT DEFAULT '{}',
        created_at TEXT,
        updated_at TEXT
    );
    """)

    # 10. ocr_tasks (per-image homework OCR queue)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ocr_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id TEXT NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
        filename TEXT NOT NULL,
        file_path TEXT NOT NULL,
        student_id INTEGER REFERENCES students(id) ON DELETE SET NULL,
        match_status TEXT DEFAULT 'unmatched',
        match_note TEXT DEFAULT '',
        ocr_status TEXT DEFAULT 'pending',
        parsed_items_json TEXT DEFAULT '[]',
        error_msg TEXT DEFAULT '',
        created_at TEXT,
        updated_at TEXT
    );
    """)

    # Unique class + student_no index for safe incremental upsert
    try:
        cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_students_class_no
        ON students(class_name, student_no);
        """)
    except sqlite3.IntegrityError:
        pass

    # Check if students are empty, if so, seed preset demo students when enabled
    cursor.execute("SELECT COUNT(*) FROM students;")
    count = cursor.fetchone()[0]
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if count == 0 and settings.SEED_DEMO_DATA:
        seed_students = [
            ("01", "张伟", "g6_c01_s01", "六(1)班", "critical", 48.0, 52.0, "分数运算中乘法分配律掌握薄弱，找单位1偶有乘除混淆。"),
            ("08", "李芳", "g6_c01_s08", "六(1)班", "critical", 45.0, 48.0, "比的按比分配容易漏除以2，计算耐心不够。"),
            ("15", "王浩", "g6_c01_s15", "六(1)班", "critical", 52.0, 56.0, "工程问题已能找准总量为1，圆的面积公式容易把平方算成乘2。"),
            ("22", "赵静", "g6_c01_s22", "六(1)班", "critical", 55.0, 58.0, "基础概念清晰，但复杂分步计算容易出现粗心失误。"),
            ("33", "陈明", "g6_c01_s33", "六(1)班", "critical", 42.0, 46.0, "分数四则运算通分吃力，需要重点巩固口算与简算基本功。"),
        ]
        cursor.executemany("""
            INSERT INTO students (student_no, name, anonymous_code, class_name, focus_tier, baseline_score, current_est_score, l3_summary_override, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [(s[0], s[1], s[2], s[3], s[4], s[5], s[6], s[7], now_str) for s in seed_students])

    cursor.execute("SELECT COUNT(*) FROM exam_papers;")
    if cursor.fetchone()[0] == 0:
        # Preset Exam Paper
        cursor.execute("""
            INSERT INTO exam_papers (title, unit_tag, exam_date, total_score, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, ("2026秋六年级数学上册第二单元测验卷（分数四则与比）", "分数运算与比的应用", "2026-11-20", 100.0, now_str))
        paper_id = cursor.lastrowid

        # Preset Questions (20 questions)
        preset_questions = [
            ("1", "G6-CALC-01", "calc", 5.0, ["计算粗心", "约分错误", "分配律漏乘"]),
            ("2", "G6-CALC-01", "calc", 5.0, ["运算顺序错误", "符号抄错"]),
            ("3", "G6-CALC-01", "calc", 5.0, ["分配律符号混淆", "通分错误"]),
            ("4", "G6-CALC-01", "calc", 5.0, ["未化为最简分数", "除法未变倒数"]),
            ("5", "G6-FRAC-01", "fraction", 6.0, ["找错单位1", "乘除混淆"]),
            ("6", "G6-FRAC-01", "fraction", 6.0, ["找错单位1", "数量关系不清"]),
            ("7", "G6-FRAC-01", "fraction", 6.0, ["工程问题总量未设1", "效率相加算错"]),
            ("8", "G6-FRAC-01", "fraction", 6.0, ["连续分率找错中间量", "漏写答语"]),
            ("9", "G6-RATIO-01", "ratio", 5.0, ["比的基本性质混淆", "化简比算错"]),
            ("10", "G6-RATIO-01", "ratio", 5.0, ["求比值与化简比混淆", "比号写成除号"]),
            ("11", "G6-RATIO-01", "ratio", 6.0, ["周长未除以2", "比前后项颠倒"]),
            ("12", "G6-RATIO-01", "ratio", 6.0, ["按比分配份数算错", "漏求长方形面积"]),
            ("13", "G6-GEO-01", "geometry", 5.0, ["半径与直径混淆", "公式记错"]),
            ("14", "G6-GEO-01", "geometry", 5.0, ["周长与面积公式混淆", "漏写单位"]),
            ("15", "G6-GEO-01", "geometry", 6.0, ["平方算成乘2", "3.14小数乘法算错"]),
            ("16", "G6-GEO-01", "geometry", 6.0, ["圆环漏加小路宽", "外内半径颠倒"]),
            ("17", "G6-CALC-01", "calc", 5.0, ["解简易方程移项变号错误"]),
            ("18", "G6-CALC-01", "calc", 5.0, ["解方程两边未同乘"]),
            ("19", "G6-FRAC-01", "fraction", 7.0, ["分数综合应用题理解偏差", "步骤不完整"]),
            ("20", "G6-GEO-01", "geometry", 8.0, ["组合图形面积相减算错", "步骤分扣减"]),
        ]
        cursor.executemany("""
            INSERT INTO exam_questions (paper_id, q_no, kb_mother_id, module_tag, max_score, quick_error_tags)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [(paper_id, q[0], q[1], q[2], q[3], json.dumps(q[4], ensure_ascii=False)) for q in preset_questions])

    cursor.execute("SELECT COUNT(*) FROM variant_pool;")
    if cursor.fetchone()[0] == 0:
        # Preset Variant Pool Seeds
        preset_variants = [
            ("G6-CALC-01", "calc", "黄冈密卷 变式精选", "计算：$\\frac{4}{7} \\times \\frac{3}{8} + \\frac{4}{7} \\times \\frac{5}{8}$", "", "原式 $= \\frac{4}{7} \\times (\\frac{3}{8} + \\frac{5}{8}) = \\frac{4}{7} \\times 1 = \\frac{4}{7}$", 1, now_str),
            ("G6-FRAC-01", "fraction", "期末真题汇编", "一条公路长 60 千米，第一天修了全长的 $\\frac{1}{4}$，第二天修了全长的 $\\frac{1}{3}$。两天一共修了多少千米？", "", "$60 \\times (\\frac{1}{4} + \\frac{1}{3}) = 60 \\times \\frac{7}{12} = 35$（千米）。\\n答：两天一共修了 35 千米。", 1, now_str),
            ("G6-RATIO-01", "ratio", "名校名师AB卷", "一个长方形花坛周长是 36 米，长和宽的比是 $2:1$。这个花坛的面积是多少平方米？", "", "长宽和：$36 \\div 2 = 18$ 米；\\n总份数：$2 + 1 = 3$；\\n长：$18 \\times \\frac{2}{3} = 12$ 米；\\n宽：$18 \\times \\frac{1}{3} = 6$ 米；\\n面积：$12 \\times 6 = 72$ 平方米。\\n答：面积是 72 平方米。", 1, now_str),
            ("G6-GEO-01", "geometry", "单元达标冲刺", "一个圆形花坛的周长是 12.56 米，它的面积是多少平方米？（$\\pi = 3.14$）", "", "半径：$r = 12.56 \\div 3.14 \\div 2 = 2$ 米；\\n面积：$3.14 \\times 2^2 = 3.14 \\times 4 = 12.56$ 平方米。\\n答：面积是 12.56 平方米。", 1, now_str),
        ]
        cursor.executemany("""
            INSERT INTO variant_pool (kb_mother_id, module_tag, source_label, question_md, image_path, answer_steps_md, verified, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, preset_variants)

    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully.")
