#!/usr/bin/env python3
"""
Lightweight attachment upload bridge for DeepSeek Harness (DSH).
Uploads grade sheets (.jpg/.png/.csv/.xlsx) or homework batches (.zip/images)
from DSH's attachment path to grade6-math-lite, which runs Tencent OCR locally
and returns strictly desensitized JSON back to DSH.
"""
import argparse
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
import uuid


def _encode_multipart(fields, files):
    boundary = f"----dshmath{uuid.uuid4().hex}"
    chunks = []
    for k, v in fields:
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for field_name, file_path in files:
        filename = os.path.basename(file_path)
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        with open(file_path, "rb") as fp:
            data = fp.read()
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n".encode()
        )
        chunks.append(data)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def main():
    parser = argparse.ArgumentParser(description="Upload DSH attachments to grade6-math-lite")
    parser.add_argument("--type", dest="batch_type", default="dsh_grade_sheet", choices=["dsh_grade_sheet", "homework_batch"])
    parser.add_argument("--title", default="DSH 批量处理任务")
    parser.add_argument("files", nargs="+", help="Attachment file paths")
    args = parser.parse_args()

    base_url = os.environ.get("MATH_API_BASE", "").rstrip("/")
    token = os.environ.get("MATH_MCP_TOKEN", "")
    if not base_url or not token:
        print(json.dumps({"error": "MATH_API_BASE or MATH_MCP_TOKEN is not configured"}, ensure_ascii=False))
        sys.exit(1)

    for fp in args.files:
        if not os.path.isfile(fp):
            print(json.dumps({"error": f"File not found: {fp}"}, ensure_ascii=False))
            sys.exit(1)

    body, content_type = _encode_multipart(
        fields=[("batch_type", args.batch_type), ("title", args.title)],
        files=[("files", fp) for fp in args.files],
    )
    req = urllib.request.Request(
        f"{base_url}/api/batches/from-dsh",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            print(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(json.dumps({"error": f"HTTP {exc.code}", "detail": exc.read().decode("utf-8", errors="ignore")}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
