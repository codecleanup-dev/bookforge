#!/usr/bin/env python3
"""bookforge slop lint — 챕터 md의 AI-tell 지문(기계 문체 흔적) 검사. G13의 판정 엔진.

Usage:
    python3 slop_lint.py <book_dir> [--json]

패턴 정의: <SKILL>/styles/slop-patterns.json 이 기본이고,
<book_dir>/slop-patterns.json 이 있으면 그 파일이 통째로 우선한다(병합 아님 — 판정 재현성).

모드: book.json "slop_lint" = "strict" | "warn" | "off" (없으면 "warn").
  strict — fail 레벨 지문 발견 시 exit 2 (qc_gate G13 하드 실패와 동일 판정)
  warn   — 보고만 하고 exit 0
  off    — 검사 생략

검사 범위: chapters/*.md 본문. 코드 펜스(```)와 인라인 코드(`...`)는 제외한다 —
코드 안의 기호는 문체가 아니라 코드다.
"""
import json
import re
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent

FENCE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE = re.compile(r"`[^`\n]*`")

VALID_MODES = {"strict", "warn", "off"}
VALID_LEVELS = {"fail", "warn"}


def load_patterns(book_dir: Path):
    """패턴 정의 로드. 설정 오류(미지 level·깨진 regex)는 fail-closed — 조용히 건너뛰면
    strict 게이트가 fail-open이 되므로 즉시 중단한다."""
    for cand in (book_dir / "slop-patterns.json", SKILL / "styles" / "slop-patterns.json"):
        if cand.exists():
            data = json.loads(cand.read_text(encoding="utf-8"))
            pats = []
            for p in data.get("patterns", []):
                level = p.get("level", "warn")
                if level not in VALID_LEVELS:
                    sys.exit(f"slop-patterns: 알 수 없는 level '{level}' (id={p.get('id')}) "
                             f"— {sorted(VALID_LEVELS)} 중 하나여야 합니다 ({cand})")
                try:
                    rx = re.compile(p["regex"])
                except re.error as e:
                    sys.exit(f"slop-patterns: 잘못된 정규식 (id={p.get('id')}): {e} ({cand})")
                pats.append({"id": p["id"], "re": rx, "level": level,
                             "desc": p.get("desc", "")})
            return pats, str(cand)
    return [], None


def _mask_code(line: str) -> str:
    """인라인 코드 구간을 같은 길이의 공백으로 치환 — 열 위치 보존."""
    return INLINE_CODE.sub(lambda m: " " * len(m.group(0)), line)


def lint_book(book_dir: Path) -> dict:
    book_dir = Path(book_dir)
    book = json.loads((book_dir / "book.json").read_text(encoding="utf-8"))
    mode = book.get("slop_lint", "warn")
    if mode not in VALID_MODES:
        # 오타("strcit" 등)가 조용히 비-strict로 동작하면 게이트가 fail-open — 즉시 중단
        sys.exit(f"book.json slop_lint: 알 수 없는 모드 '{mode}' — "
                 f"{sorted(VALID_MODES)} 중 하나여야 합니다")
    patterns, source = load_patterns(book_dir)
    findings = []
    if mode != "off" and patterns:
        for md in sorted((book_dir / "chapters").glob("*.md")):
            in_fence = False
            for lineno, line in enumerate(md.read_text(encoding="utf-8").split("\n"), 1):
                if FENCE.match(line):
                    in_fence = not in_fence
                    continue
                if in_fence:
                    continue
                masked = _mask_code(line)
                for p in patterns:
                    for m in p["re"].finditer(masked):
                        lo, hi = max(0, m.start() - 18), min(len(line), m.end() + 18)
                        findings.append({
                            "file": md.name, "line": lineno, "col": m.start() + 1,
                            "id": p["id"], "level": p["level"], "desc": p["desc"],
                            "match": line[m.start():m.end()],
                            "excerpt": line[lo:hi].strip(),
                        })
    counts = {"fail": sum(1 for f in findings if f["level"] == "fail"),
              "warn": sum(1 for f in findings if f["level"] == "warn")}
    return {"mode": mode, "patterns_from": source, "findings": findings, "counts": counts}


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: python3 scripts/slop_lint.py <book_dir> [--json]")
    result = lint_book(Path(sys.argv[1]).resolve())
    if "--json" in sys.argv[2:]:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for f in result["findings"]:
            print(f"{f['level'].upper():4s} {f['file']}:{f['line']}:{f['col']} "
                  f"[{f['id']}] …{f['excerpt']}…")
        print(f"slop-lint: mode={result['mode']} fail={result['counts']['fail']} "
              f"warn={result['counts']['warn']}")
    if result["mode"] == "strict" and result["counts"]["fail"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
