#!/usr/bin/env python3
"""bookforge scaffold: create a new book project directory.

Usage: python3 scaffold.py <book_dir> --style practical --title "제목" \
         [--subtitle S] [--length short|standard|long] [--author A] [--brand "#hex"]
Creates book.json, outline.json (stub), chapters/, assets/.
"""
import argparse, json
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument("book_dir")
    p.add_argument("--style", required=True,
                   choices=["practical", "insight", "academic", "essay", "business", "magazine"])
    p.add_argument("--title", required=True)
    p.add_argument("--subtitle", default=None)
    p.add_argument("--length", default="short", choices=["short", "standard", "long"])
    p.add_argument("--author", default="bookforge")
    p.add_argument("--brand", default=None)
    p.add_argument("--date", default=None)
    a = p.parse_args()

    d = Path(a.book_dir).resolve()
    (d / "chapters").mkdir(parents=True, exist_ok=True)
    (d / "assets").mkdir(exist_ok=True)

    book = {"title": a.title, "subtitle": a.subtitle, "author": a.author,
            "style": a.style, "length": a.length, "images": "vector",
            "slop_lint": "strict"}
    if a.brand:
        book["brand"] = a.brand
    if a.date:
        book["date"] = a.date
    (d / "book.json").write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")

    outline = {"chapters": [
        {"file": "ch-01.md", "title": "1장 제목", "summary": "장 요약 1~2문장 (도비라에 실림)"},
    ]}
    op = d / "outline.json"
    if not op.exists():
        op.write_text(json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK scaffold: {d}")

if __name__ == "__main__":
    main()
