#!/usr/bin/env python3
"""bookforge HTML engine: md -> themed HTML -> Chromium print (2-pass TOC).

Theme contract (styles/<style>/):
  theme.css   — full print stylesheet (@page, tokens via :root, component classes)
  theme.html  — python str.Template page skeleton with $title $subtitle $author
                $date $brand $toc $body $fonts_dir placeholders
Chapter markers: each chapter opener embeds an invisible ⟦chNN⟧ marker; pass 1
extracts real page numbers with PyMuPDF, injects them into .tocpg spans, pass 2
prints the final PDF.
"""
import json, os, re, subprocess, sys
from html import escape as _esc
from pathlib import Path
from string import Template

import fitz
from markdown_it import MarkdownIt

MD = MarkdownIt("commonmark", {"html": True, "typographer": True}) \
    .enable("table").enable("strikethrough")
SAFE_MD = MarkdownIt("commonmark", {"html": False, "typographer": True}) \
    .enable("table").enable("strikethrough")

CALLOUT_RE = re.compile(r"^:::\s*(info|tip|warn|quote|stat|pull)\s*(.*)$")

def md_to_html(md: str, *, allow_html: bool = True) -> str:
    """markdown subset -> html, with ::: callout directive support.

    ``allow_html=False`` is the untrusted manuscript-preview contract. The print
    pipeline keeps the historical raw-HTML behavior unless the caller opts out.
    """
    parser = MD if allow_html else SAFE_MD
    out, lines, buf = [], md.split("\n"), []
    def flush():
        if buf:
            out.append(parser.render("\n".join(buf)))
            buf.clear()
    i = 0
    while i < len(lines):
        m = CALLOUT_RE.match(lines[i].strip())
        if m:
            flush()
            kind, title = m.group(1), m.group(2).strip()
            body, i = [], i + 1
            while i < len(lines) and lines[i].strip() != ":::":
                body.append(lines[i]); i += 1
            i += 1
            if kind == "pull":
                ls = [l.strip() for l in body if l.strip()]
                quote_t = _esc(ls[0]) if ls else ""
                speaker = _esc(ls[1]) if len(ls) > 1 else ""
                sp = f'<div class="pull-speaker">{speaker}</div>' if speaker else ""
                out.append(f'<section class="pullquote"><div class="pull-text">{quote_t}</div>{sp}</section>')
            elif kind == "stat":
                ls = [l.strip() for l in body if l.strip()]
                value = _esc(ls[0]) if ls else ""
                label = _esc(ls[1]) if len(ls) > 1 else ""
                out.append(f'<div class="stat"><span class="stat-value">{value}</span>'
                           f'<span class="stat-label">{label}</span></div>')
            else:
                t = f'<div class="callout-title">{_esc(title)}</div>' if title else ""
                out.append(f'<div class="callout callout-{kind}">{t}'
                           f'{parser.render(chr(10).join(body))}</div>')
        else:
            buf.append(lines[i]); i += 1
    flush()
    html = "\n".join(out)
    # 이미지 문단 -> figure/figcaption (alt=캡션, title="출처: …")
    def fig(m):
        src, alt, title = m.group("src"), m.group("alt") or "", m.group("title") or ""
        cap = alt
        if title:
            cap = f"{cap} · {title}" if cap else title
        c = f"<figcaption>{cap}</figcaption>" if cap else ""
        return f'<figure><img src="{src}" alt="{alt}">{c}</figure>'
    html = re.sub(
        r'<p><img src="(?P<src>[^"]+)" alt="(?P<alt>[^"]*)"(?: title="(?P<title>[^"]*)")?\s*/?></p>',
        fig, html)
    return html

def build(book_dir: Path, book: dict, outline: dict, style_dir: Path, skill: Path):
    ts = book_dir / "typeset"
    ts.mkdir(exist_ok=True)
    (book_dir / "draft").mkdir(exist_ok=True)

    tokens = json.loads((style_dir / "tokens.json").read_text(encoding="utf-8"))
    key = book.get("brand") or tokens.get("brand_default", "#0E7C7B")
    r_, g_, b_ = (int(key.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    cover_img = book_dir / "assets" / "cover.png"
    if not cover_img.exists():
        cover_img = book_dir / "assets" / "cover.jpg"

    tpl = Template((style_dir / "theme.html").read_text(encoding="utf-8"))
    css = Template((style_dir / "theme.css").read_text(encoding="utf-8")).safe_substitute(
        fonts_dir=(skill / "assets" / "fonts").as_uri(),
        key_color=key,
        key_tint=f"rgba({r_},{g_},{b_},0.08)",
    )

    # refit-params.json: 장별 자간 미세조정(pagination.md §5 L2). 주의 — inline
    # letter-spacing은 테마 기본값을 대체하므로 refit.py가 (테마 기본 + Δ) 절대값을 준다.
    refit = {}
    rp = book_dir / "refit-params.json"
    if rp.exists():
        refit = json.loads(rp.read_text(encoding="utf-8"))

    toc_items, sections, tocmap_items, first_pull = [], [], [], None
    for idx, ch in enumerate(outline["chapters"], 1):
        mk = f"ch{idx:02d}"
        src = book_dir / "chapters" / ch["file"]
        raw = src.read_text(encoding="utf-8")
        # strip the leading H1 (title comes from outline)
        raw = re.sub(r"^#\s+.*\n", "", raw, count=1)
        img_m = re.search(r"!\[[^\]]*\]\((\.\./assets/[^) \"]+)", raw)
        # 목차 이미지 맵(magazine $tocmap): 챕터 첫 컷 4~6장. 캡션은 해당 쪽번호만 —
        # 좌측 리스트와 같은 .tocpg[data-mk] 마크업이라 2-pass 스탬핑이 같이 채운다.
        if img_m and len(tocmap_items) < 5:
            tocmap_items.append(
                f'<figure><img src="{img_m.group(1)}" alt="">'
                f'<figcaption><span class="tocpg" data-mk="{mk}">00</span></figcaption></figure>')
        if first_pull is None:
            pm = re.search(r"^::: pull\n(.+)$", raw, re.M)
            if pm:
                first_pull = pm.group(1).strip()
        body_html = md_to_html(raw)
        # 표 캡션 계약: 콘텐츠가 "[표] 제목 | 자료: 출처" 문단을 준 표만 라벨을 단다.
        # 자동 필러 라벨 금지 — 캡션 없는 표는 라벨 없이 그대로 렌더된다.
        tno = [0]
        def wrap_tbl(m):
            tno[0] += 1
            title = m.group(1).strip()
            source = (m.group(2) or "").strip()
            src_html = f'<div class="tbl-source">자료: {source}</div>' if source else ""
            return (f'<div class="tablewrap"><div class="tbl-caption">'
                    f'<span class="no">표 {idx}-{tno[0]}.</span> {title}</div>'
                    f'{m.group(3)}{src_html}</div>')
        body_html = re.sub(
            r"<p>\[표\]\s*(.+?)(?:\s*\|\s*자료\s*[:：]\s*(.+?))?</p>\s*(<table>.*?</table>)",
            wrap_tbl, body_html, flags=re.S)
        # 전면 요소(풀퀘트)는 다단 chapter-body 밖으로 분리
        body_html = re.sub(
            r'(<section class="pullquote">.*?</section>)',
            r'</div>\1<div class="chapter-body">',
            body_html, flags=re.S)
        summary = ch.get("summary") or ""
        sec = (
            f'<section class="chapter" id="{mk}">\n'
            f'<div class="opener"><span class="pgmark">@@{mk}@@</span>'
            f'<div class="opener-num">{idx:02d}</div>'
            f'<h1 class="opener-title">{ch["title"]}</h1>'
            f'<p class="opener-summary">{summary}</p></div>\n'
            f'<div class="chapter-body">{body_html}</div>\n</section>')
        # 풀퀘트 분리로 생긴 빈 chapter-body 제거 (백지면 방지) — refit 주입보다 먼저
        sec = re.sub(r'<div class="chapter-body">\s*</div>', "", sec)
        prm = refit.get(Path(ch["file"]).stem, {})
        if prm.get("letter_spacing_em") is not None:
            sec = sec.replace('<div class="chapter-body">',
                              f'<div class="chapter-body" style="letter-spacing:{prm["letter_spacing_em"]}em">')
        sections.append(sec)
        # data-sum: 목차 한줄 카피. outline의 toc_line(목차 전용 완결 카피)이 있으면 그것을,
        # 없으면 summary 40자 말줄임. 속성이라 이를 쓰지 않는 테마는 무시한다.
        tsum = re.sub(r"\s+", " ", ch.get("toc_line") or summary).strip()
        if len(tsum) > 40:
            tsum = tsum[:40].rstrip(" ,.·") + "…"
        toc_items.append(
            f'<li data-sum="{_esc(tsum, quote=True)}"><span class="toc-title">{ch["title"]}</span>'
            f'<span class="toc-leader"></span>'
            f'<span class="tocpg" data-mk="{mk}">00</span></li>')

    html = tpl.substitute(
        title=book.get("title", ""), subtitle=book.get("subtitle") or "",
        author=book.get("author", "bookforge"), date=book.get("date", ""),
        brand=key,
        cover_art=f"background-image:url('{cover_img.as_uri()}')" if cover_img.exists() else "",
        toc="<ol class=\"toc\">" + "\n".join(toc_items) + "</ol>",
        tocmap="\n".join(tocmap_items),
        backquote=book.get("backquote") or first_pull or book.get("subtitle") or "",
        body="\n".join(sections),
        css=css,
    )
    page1 = ts / "book.html"
    page1.write_text(html, encoding="utf-8")

    env = dict(os.environ)
    env["NODE_PATH"] = subprocess.run(["npm", "root", "-g"], capture_output=True,
                                      text=True).stdout.strip()
    printer = skill / "scripts" / "print_pdf.mjs"
    pdf1 = ts / "pass1.pdf"
    r = subprocess.run(["node", str(printer), str(page1), str(pdf1)],
                       capture_output=True, text=True, env=env)
    if r.returncode != 0:
        sys.exit("HTML pass1 print failed:\n" + r.stderr)

    # pass 1: locate markers
    doc = fitz.open(pdf1)
    pages = {}
    for pno in range(doc.page_count):
        norm = re.sub(r"\s+", "", doc[pno].get_text())
        for m in re.findall(r"@@(ch\d+)@@", norm):
            pages.setdefault(m, pno + 1)
    doc.close()

    # magazine convention: absolute page numbers, cover = page 1
    html2 = html
    for mk, abs_page in pages.items():
        html2 = html2.replace(f'<span class="tocpg" data-mk="{mk}">00</span>',
                              f'<span class="tocpg" data-mk="{mk}">{abs_page}</span>')
    # pass 2에는 마커 불필요 — 잉크·텍스트 레이어 오염 방지를 위해 제거
    # (.pgmark은 absolute 포지션이라 제거해도 리플로우 없음; 북마크는 pass 1 페이지맵 사용)
    html2 = re.sub(r'<span class="pgmark">@@ch\d+@@</span>', "", html2)
    page2 = ts / "book-final.html"
    page2.write_text(html2, encoding="utf-8")

    out = book_dir / "draft" / "book.pdf"
    r = subprocess.run(["node", str(printer), str(page2), str(out)],
                       capture_output=True, text=True, env=env)
    if r.returncode != 0:
        sys.exit("HTML pass2 print failed:\n" + r.stderr)

    # PDF outline(bookmarks): Chromium print emits none — stamp from markers
    doc = fitz.open(out)
    toc = []
    for idx, ch in enumerate(outline["chapters"], 1):
        mk = f"ch{idx:02d}"
        if mk in pages:
            toc.append([1, ch["title"], pages[mk]])
    if toc:
        doc.set_toc(toc)
    doc.saveIncr()
    doc.close()

    # optional theme post-decoration (running marks, folio) via PyMuPDF
    dec = style_dir / "decorate.py"
    if dec.exists():
        import importlib.util
        spec = importlib.util.spec_from_file_location("theme_decorate", dec)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        doc = fitz.open(out)
        mod.decorate(doc, {"book": book, "pages": pages,
                           "fonts_dir": skill / "assets" / "fonts"})
        doc.saveIncr()
        doc.close()
    print(f"OK draft: {out} (chapter pages: {pages})")
