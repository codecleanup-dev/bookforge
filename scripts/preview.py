#!/usr/bin/env python3
"""bookforge manuscript preview — P1.5 원고 검수용 연속 스크롤 HTML (엔진 무관).

Usage: python3 preview.py <book_dir>   ->  <book_dir>/preview/manuscript.html

목적: 조판 협상(P2-4)에 들어가기 전에 사람이 내용·문장을 확정하는 검수 표면.
조판 후 문장 수정은 표 높이·페이지 분할을 되돌려 게이트 라운드를 재유발하므로,
문장 품질(slop 포함)은 여기서 끝내는 것이 싸다.

- Typst/HTML 트랙 공통 (테마 무관, 인쇄용 아님 — 페이지 분할 없음)
- slop_lint 결과를 상단 패널 + 본문 하이라이트로 표시
- 장별 글자수(공백 제외)와 합계 표기
- 이미지는 챕터 md의 ../assets/ 상대경로가 preview/ 에서도 그대로 풀린다
"""
import html as _html
import json
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_html import md_to_html  # noqa: E402
from slop_lint import (SlopLintError, lint_book,  # noqa: E402
                       resolve_chapter_files)

CSS = """
:root { --ink:#20242c; --sub:#5c6070; --line:rgba(32,36,44,.14); --mark:#ffe08a;
        --fail:#c0392b; --warn:#8a6d1a; --bg:#fbfbf9; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font-family:'Pretendard Variable',Pretendard,-apple-system,'Apple SD Gothic Neo',sans-serif;
  line-height:1.75; font-size:16px; }
.wrap { max-width:760px; margin:0 auto; padding:48px 24px 120px; }
header.book { border-bottom:2px solid var(--ink); padding-bottom:20px; margin-bottom:8px; }
header.book h1 { font-size:30px; margin:0 0 6px; }
header.book .sub { color:var(--sub); }
.meta { color:var(--sub); font-size:13px; margin-top:8px; }
.lintpanel { border:1px solid var(--line); background:#fff; border-radius:8px;
  padding:16px 20px; margin:24px 0; font-size:14px; }
.lintpanel h2 { font-size:15px; margin:0 0 10px; }
.lintpanel .ok { color:#2e7d32; }
.lintpanel li { margin:3px 0; }
.lintpanel .lv-fail { color:var(--fail); font-weight:700; }
.lintpanel .lv-warn { color:var(--warn); font-weight:700; }
.chapter { margin-top:56px; }
.ch-head { border-top:1px solid var(--line); padding-top:28px; }
.ch-num { font-size:13px; letter-spacing:.12em; color:var(--sub); }
.ch-head h1 { font-size:24px; margin:4px 0 8px; }
.ch-sum { color:var(--sub); font-style:normal; margin:0 0 4px; }
.ch-stat { font-size:12px; color:var(--sub); margin-bottom:20px; }
h2 { font-size:19px; margin-top:36px; }
h3 { font-size:16px; margin-top:28px; }
table { border-collapse:collapse; width:100%; font-size:14px; margin:16px 0; }
th, td { border:1px solid var(--line); padding:8px 10px; text-align:left; vertical-align:top; }
th { background:#f0f0ec; }
figure { margin:20px 0; }
figure img { max-width:100%; }
figcaption { font-size:13px; color:var(--sub); margin-top:6px; }
blockquote { border-left:3px solid var(--line); margin:16px 0; padding:2px 16px; color:var(--sub); }
code { background:#f0f0ec; padding:1px 5px; border-radius:4px; font-size:.9em; }
.callout { border:1px solid var(--line); border-radius:8px; padding:12px 16px; margin:16px 0;
  background:#fff; font-size:15px; }
.callout-title { font-weight:700; margin-bottom:4px; }
.pullquote { margin:24px 0; padding:16px 20px; border-top:2px solid var(--ink);
  border-bottom:2px solid var(--ink); font-size:18px; font-weight:600; }
.stat { display:inline-block; border:1px solid var(--line); border-radius:8px;
  padding:10px 16px; margin:8px 8px 8px 0; background:#fff; }
.stat-value { display:block; font-size:22px; font-weight:800; }
.stat-label { display:block; font-size:12px; color:var(--sub); }
.tbl-caption { font-size:13px; font-weight:700; margin:16px 0 6px; }
.tbl-source { font-size:12px; color:var(--sub); margin-top:4px; }
mark.slop { background:var(--mark); outline:2px solid var(--mark); }
"""


def _stats(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def _wrap_tables(body_html: str, ch_idx: int) -> str:
    tno = [0]

    def wrap(m):
        tno[0] += 1
        title, source = m.group(1).strip(), (m.group(2) or "").strip()
        src_html = f'<div class="tbl-source">자료: {source}</div>' if source else ""
        return (f'<div class="tbl-caption">표 {ch_idx}-{tno[0]}. {title}</div>'
                f'{m.group(3)}{src_html}')
    return re.sub(
        r"<p>\[표\]\s*(.+?)(?:\s*\|\s*자료\s*[:：]\s*(.+?))?</p>\s*(<table>.*?</table>)",
        wrap, body_html, flags=re.S)


def _mark_findings(source: str, findings: list, first_line: int) -> tuple[str, str | None]:
    """finding의 실제 행·열에 렌더 후 치환할 고유 마커를 삽입한다.

    같은 문자열의 비검출 위치나 코드 영역까지 하이라이트하던 전역 문자열 치환을 피한다.
    """
    lines = source.splitlines(keepends=True)
    by_line = {}
    for finding in findings:
        line_idx = finding["line"] - first_line
        if 0 <= line_idx < len(lines):
            start = finding["col"] - 1
            end = start + len(finding["match"])
            if lines[line_idx][start:end] == finding["match"] and start < end:
                by_line.setdefault(line_idx, set()).add((start, end))

    nonce = secrets.token_hex(10)
    marker_no = 0
    for line_idx, ranges in by_line.items():
        selected = []
        last_end = -1
        for start, end in sorted(ranges):
            if start < last_end:
                continue
            selected.append((start, end))
            last_end = end
        parts = []
        cursor = 0
        for start, end in selected:
            marker_no += 1
            opening = f"\ue000bf-{nonce}-{marker_no}-open\ue001"
            closing = f"\ue000bf-{nonce}-{marker_no}-close\ue001"
            parts.extend((lines[line_idx][cursor:start], opening,
                          lines[line_idx][start:end], closing))
            cursor = end
        parts.append(lines[line_idx][cursor:])
        lines[line_idx] = "".join(parts)
    return "".join(lines), nonce if marker_no else None


def _render_marks(body_html: str, nonce: str | None) -> str:
    if nonce is None:
        return body_html
    marker = re.compile(rf"\ue000bf-{re.escape(nonce)}-\d+-(open|close)\ue001")
    return marker.sub(lambda match: '<mark class="slop">' if match.group(1) == "open"
                      else "</mark>", body_html)


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: python3 scripts/preview.py <book_dir>")
    book_dir = Path(sys.argv[1]).resolve()
    try:
        book = json.loads((book_dir / "book.json").read_text(encoding="utf-8"))
        outline = json.loads((book_dir / "outline.json").read_text(encoding="utf-8"))
        chapter_paths = dict(resolve_chapter_files(book_dir, outline))
        lint = lint_book(book_dir)
    except (OSError, UnicodeError, json.JSONDecodeError, SlopLintError) as exc:
        sys.exit(f"preview: {exc} (fail-closed)")
    by_file = {}
    for f in lint["findings"]:
        by_file.setdefault(f["file"], []).append(f)

    total = 0
    sections = []
    for idx, ch in enumerate(outline["chapters"], 1):
        src = chapter_paths[ch["file"]]
        raw = src.read_text(encoding="utf-8")
        heading = re.match(r"^#\s+.*(?:\n|$)", raw)
        raw_body = raw[heading.end():] if heading else raw
        first_body_line = raw[:heading.end()].count("\n") + 1 if heading else 1
        n = _stats(raw_body)
        total += n
        marked_body, marker_nonce = _mark_findings(
            raw_body, by_file.get(ch["file"], []), first_body_line)
        body = md_to_html(marked_body, allow_html=False)
        body = _wrap_tables(body, idx)
        body = _render_marks(body, marker_nonce)
        sections.append(
            f'<section class="chapter" id="ch{idx:02d}">'
            f'<div class="ch-head"><div class="ch-num">{idx:02d} · {_html.escape(ch["file"])}</div>'
            f'<h1>{_html.escape(ch["title"])}</h1>'
            f'<p class="ch-sum">{_html.escape(ch.get("summary") or "")}</p>'
            f'<div class="ch-stat">본문 {n:,}자 (공백 제외)</div></div>'
            f'{body}</section>')

    if lint["mode"] == "off":
        lint_html = '<div class="lintpanel"><h2>slop 검사</h2><span>off (book.json)</span></div>'
    elif not lint["findings"]:
        lint_html = ('<div class="lintpanel"><h2>slop 검사</h2>'
                     '<span class="ok">지문 검출 0건 · 통과</span></div>')
    else:
        # outline 밖의 chapters/*.md에서 나온 지문은 앵커 없이 표시 (크래시 금지)
        ch_index = {c["file"]: i for i, c in enumerate(outline["chapters"], 1)}
        items = []
        for f in lint["findings"]:
            name = _html.escape(f["file"])
            idx = ch_index.get(f["file"])
            loc = f'<a href="#ch{idx:02d}">{name}</a>' if idx else name
            items.append(
                f'<li><span class="lv-{f["level"]}">{f["level"].upper()}</span> '
                f'{loc}:{f["line"]} [{_html.escape(f["id"])}] '
                f'…{_html.escape(f["excerpt"])}…</li>')
        lint_html = (f'<div class="lintpanel"><h2>slop 검사: fail {lint["counts"]["fail"]} · '
                     f'warn {lint["counts"]["warn"]} (모드 {lint["mode"]})</h2>'
                     f'<ul>{"".join(items)}</ul>'
                     f'<div class="meta">본문 하이라이트 = 검출 지점. fail은 strict 모드에서 '
                     f'G13 하드 실패, warn은 사람 판단.</div></div>')

    style_nonce = secrets.token_urlsafe(18)
    doc = (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        # 검수 표면은 데이터 뷰어다 — 원고 유래 HTML이 섞여도 스크립트 실행·원격 요청·
        # CSS/iframe 오버레이(검수 패널 은폐)를 전부 막는다. 이미지는 로컬(file:/data:)만.
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        f'img-src file: data:; style-src \'nonce-{style_nonce}\'; script-src \'none\'; '
        'object-src \'none\'; frame-src \'none\'; base-uri \'none\'; form-action \'none\'">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>원고 검수 · {_html.escape(book.get("title", ""))}</title>'
        f'<style nonce="{style_nonce}">{CSS}</style></head><body><div class="wrap">'
        f'<header class="book"><h1>{_html.escape(book.get("title", ""))}</h1>'
        f'<div class="sub">{_html.escape(book.get("subtitle") or "")}</div>'
        f'<div class="meta">P1.5 원고 검수용 (페이지 분할 없음) · '
        f'{_html.escape(str(book.get("style", "")))} · '
        f'{_html.escape(str(book.get("length", "")))} · 총 {total:,}자 (공백 제외) · '
        f'장 {len(outline["chapters"])}개'
        f'</div></header>'
        f'{lint_html}'
        f'{"".join(sections)}'
        '</div></body></html>')

    out_dir = book_dir / "preview"
    if out_dir.is_symlink():
        sys.exit("preview/ 가 심볼릭 링크입니다 — 실제 디렉토리여야 합니다")
    try:
        out_dir.mkdir(exist_ok=True)
    except OSError as exc:
        sys.exit(f"preview/ 디렉토리를 만들 수 없습니다: {exc}")
    if out_dir.is_symlink() or not out_dir.is_dir():
        sys.exit("preview/ 가 실제 디렉토리가 아닙니다")
    out = out_dir / "manuscript.html"
    # 예측 불가능한 O_EXCL 임시파일 + 원자 교체: 선점 symlink와 동시 실행을 모두 피한다.
    tmp = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=".manuscript.", suffix=".tmp", dir=out_dir)
        tmp = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(doc)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, out)
        tmp = None
    except OSError as exc:
        sys.exit(f"preview HTML을 원자적으로 쓸 수 없습니다: {exc}")
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    print(f"OK preview: {out} (총 {total:,}자, slop fail {lint['counts']['fail']} · "
          f"warn {lint['counts']['warn']})")


if __name__ == "__main__":
    main()
