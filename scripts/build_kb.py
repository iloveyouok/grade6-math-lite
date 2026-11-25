import os
import sys
import re
import json
import hashlib
import numpy as np
import httpx

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.config import settings

def parse_md_frontmatter(content: str):
    """Parses YAML frontmatter from markdown string."""
    pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
    match = re.match(pattern, content, re.DOTALL)
    if not match:
        return {}, content

    fm_raw = match.group(1)
    body = match.group(2)
    meta = {}
    for line in fm_raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip()
            if val.startswith("[") and val.endswith("]"):
                items = [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
                meta[key] = items
            else:
                meta[key] = val.strip("'\"")
    return meta, body

def generate_pseudo_embedding(text: str, dim: int = 128) -> list:
    """
    Generates a deterministic pseudo-embedding based on character n-grams and math keywords.
    Guarantees reproducible cosine similarity without external dependencies.
    """
    vec = np.zeros(dim, dtype=np.float32)
    if not text:
        return vec.tolist()

    # Domain keywords for Grade 6 math
    keywords = {
        "计算": 10, "分数": 11, "简便": 12, "分配律": 13, "混合运算": 14, "约分": 15, "乘法": 16, "除法": 17,
        "工程": 20, "合作": 21, "天数": 22, "单位1": 23, "总量": 24, "分率": 25, "连续": 26, "完成": 27,
        "比": 30, "按比分配": 31, "长方形": 32, "周长": 33, "长和宽": 34, "份数": 35, "化简比": 36,
        "圆": 40, "半径": 41, "直径": 42, "面积": 43, "圆环": 44, "周长与面积": 45, "小路": 46, "3.14": 47
    }

    for kw, idx in keywords.items():
        if kw in text:
            weight = text.count(kw) * 3.0 + 2.0
            vec[idx % dim] += weight

    # Character bigram hashing
    clean_text = re.sub(r"\s+", "", text.lower())
    for i in range(len(clean_text) - 1):
        bigram = clean_text[i:i+2]
        h = int(hashlib.md5(bigram.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        vec[idx] += 1.0

    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()

def get_dashscope_embedding(text: str) -> list:
    """Calls Aliyun DashScope text-embedding-v4 API."""
    url = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
    headers = {
        "Authorization": f"Bearer {settings.DASHSCOPE_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": settings.EMBEDDING_MODEL,
        "input": text,
        "encoding_format": "float"
    }
    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=15.0)
        if resp.status_code == 200:
            data = resp.json()
            return data["data"][0]["embedding"]
        else:
            print(f"DashScope Embedding warning: {resp.status_code} {resp.text}, fallback to pseudo embedding.")
            return generate_pseudo_embedding(text)
    except Exception as e:
        print(f"DashScope Embedding error: {e}, fallback to pseudo embedding.")
        return generate_pseudo_embedding(text)

def build_kb():
    kb_dir = settings.KB_DIR
    if not os.path.exists(kb_dir):
        kb_dir = os.path.join(os.path.dirname(__file__), "..", "knowledge_base")
    
    os.makedirs(os.path.dirname(settings.KB_VECTORS_PATH), exist_ok=True)
    
    records = []
    md_files = [f for f in os.listdir(kb_dir) if f.endswith(".md")]
    md_files.sort()

    print(f"Found {len(md_files)} mother question Markdown files in {kb_dir}...")

    for f in md_files:
        path = os.path.join(kb_dir, f)
        with open(path, "r", encoding="utf-8") as fp:
            content = fp.read()
        
        meta, body = parse_md_frontmatter(content)
        mother_id = meta.get("id", f.replace(".md", ""))
        title = meta.get("title", mother_id)
        module_tag = meta.get("module_tag", "calc")
        tags = meta.get("tags", [])
        level = meta.get("level", "L1")

        search_text = f"{title} {' '.join(tags)} {body[:500]}"

        if settings.MOCK_EXTERNAL_APIS or not settings.DASHSCOPE_API_KEY or "sk-mock" in settings.DASHSCOPE_API_KEY:
            emb = generate_pseudo_embedding(search_text)
        else:
            emb = get_dashscope_embedding(search_text)

        # Extract sections
        sections = {}
        curr_sec = "intro"
        curr_lines = []
        for line in body.splitlines():
            if line.startswith("## "):
                if curr_lines:
                    sections[curr_sec] = "\n".join(curr_lines).strip()
                    curr_lines = []
                curr_sec = line.strip("# ").strip()
            else:
                curr_lines.append(line)
        if curr_lines:
            sections[curr_sec] = "\n".join(curr_lines).strip()

        records.append({
            "id": mother_id,
            "title": title,
            "module_tag": module_tag,
            "level": level,
            "tags": tags,
            "filename": f,
            "full_body": body,
            "sections": sections,
            "embedding": emb
        })
        print(f"  [+] Indexed {mother_id}: {title} (Module: {module_tag})")

    with open(settings.KB_VECTORS_PATH, "w", encoding="utf-8") as out:
        json.dump(records, out, ensure_ascii=False, indent=2)

    print(f"Successfully generated KB vectors to {settings.KB_VECTORS_PATH} with {len(records)} items.")

if __name__ == "__main__":
    build_kb()
