#!/usr/bin/env python3
"""bookforge QC gate — the ONLY path from draft/ to final/.

Usage: python3 qc_gate.py <book_dir> [--refit]
Gates (pagination.md §7):
  G10 quote   : (렌더 전) ::: pull 인용·콜아웃 수치가 챕터 본문에 실재 — 날조 차단
  G13 slop    : (렌더 전) AI-tell 지문(slop_lint.py) — book.json "slop_lint" strict|warn|off
  G1  render  : draft/book.pdf exists; page count vs preset (PLAN=hard, --refit=WARN)
  G2  fonts   : every font fully embedded
  G3  overflow: no bbox escapes the page rect (tol 1.5pt)
  G4  toc     : bookmarks match chapter start pages
  G7  density : FRAME(판면 드리프트)·BLANK(구 G5 흡수)·TAIL·MID·DOC — reach/ink/gap
  G8  stretch : 공기 채움(gap) + 행송 편차 탐지
  G9  keep    : 면 끝 제목 고립 / widow (단일단 스타일)
  G11 roles   : pageroles.json 무결성(코드·선행조건·anchor·예산)
  G12 parity  : 장 시작 직전 필러 백면 (단면 전자책에 인쇄 관습 금지)
Writes <book_dir>/gate-report.json. On PASS copies draft/book.pdf -> final/<slug>.pdf.
Exit 0 = PASS, 1 = FAIL. (G6 visual judgement is the agent's job on the contact sheet.)
"""
import json, re, shutil, sys, unicodedata
from collections import Counter
from pathlib import Path
from statistics import median

import fitz  # PyMuPDF

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pagemetrics import analyze  # noqa: E402
from slop_lint import lint_book  # noqa: E402

SKILL = Path(__file__).resolve().parent.parent
TOL = 1.5  # pt
MM2PT = 72 / 25.4

TAIL_HARD = {"essay": 0.35, "magazine": 0.35}          # default 0.45
TAIL_WARN = {"practical": 0.70, "insight": 0.70, "academic": 0.70,
             "business": 0.65, "essay": 0.55, "magazine": 0.50}
WARN_REPORT_ONLY = {"essay", "magazine", "insight"}    # 상용 꼬리 실측 표본 0인 스타일 — HARD만 강제
MID_HARD = 0.75
MID_ROLE_MIN = {"essay": 0.88, "insight": 0.85}       # default 0.90 — insight는 130mm 통짜 블록+H2 24mm 이월 물리
DOC_STATS_STYLES = {"practical", "academic"}  # 문서 통계는 실측 근거 있는 스타일만
ROLE_CODES = {"PART_DIVIDER", "FULL_BLEED_PLATE", "EXEC_SUMMARY",
              "ESSAY_BREATH", "MAGAZINE_WHITESPACE", "TOC_TAIL"}


def norm(s):
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[\s​]+", "", s)
    s = re.sub(r"[\"'‘’“”「」『』*_`~]", "", s)
    return s.replace(",", "")


def g10_quote_check(book_dir, outline):
    """렌더 전 md 검사: pull 인용 분절(≥12자)과 stat/콜아웃 수치의 본문 실재."""
    problems = []
    for ch in outline["chapters"]:
        p = book_dir / "chapters" / ch["file"]
        if not p.exists():
            continue
        raw = p.read_text(encoding="utf-8")
        callouts = re.findall(r"^:::\s*(pull|stat|quote|info|tip|warn)[^\n]*\n(.*?)^:::\s*$",
                              raw, re.S | re.M)
        body = re.sub(r"^:::.*?^:::\s*$", "", raw, flags=re.S | re.M)
        nbody = norm(body)
        body_nums = set(re.findall(r"\d[\d,.]*", body.replace(",", "")))
        for kind, ctext in callouts:
            if kind == "pull":
                # build_html 계약과 동일: 1행 = 인용문, 2행 = 화자 라벨(검사 제외)
                ls = [l.strip() for l in ctext.split("\n") if l.strip()]
                quote_line = ls[0] if ls else ""
                frags = [f for f in re.split(r"[…⋯]|\.\.\.|[—–~]", quote_line) if len(norm(f)) >= 12]
                for f in frags:
                    if norm(f) not in nbody:
                        problems.append(f"{ch['file']}: pull 인용이 본문에 없음 — '{f.strip()[:40]}'")
            if kind == "stat":
                for tok in re.findall(r"\d[\d,.]*", ctext.replace(",", "")):
                    if len(tok) < 2:  # 한 자리 토큰(5G·3nm류)은 오탐 — 검사 제외
                        continue
                    if tok in body_nums:
                        continue
                    ctx = ctext[max(0, ctext.find(tok) - 8):ctext.find(tok)]
                    approx = any(w in ctx for w in ("약", "가량", "내외", "여"))
                    if approx:
                        try:
                            v = float(tok)
                            if any(abs(float(b) - v) <= 0.05 * max(v, 1e-9)
                                   for b in body_nums if _isnum(b)):
                                continue
                        except ValueError:
                            pass
                    problems.append(f"{ch['file']}: stat 수치 '{tok}'가 본문에 없음")
    return problems


def _isnum(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: python3 scripts/qc_gate.py <book_dir> [--refit]")
    book_dir = Path(sys.argv[1]).resolve()
    refit = "--refit" in sys.argv[2:]
    book = json.loads((book_dir / "book.json").read_text(encoding="utf-8"))
    outline = json.loads((book_dir / "outline.json").read_text(encoding="utf-8"))
    style = book["style"]
    tokens = json.loads((SKILL / "styles" / style / "tokens.json").read_text(encoding="utf-8"))
    report = {"gates": {}, "warns": [], "pass": False}
    fails = []

    # ---- G10 (렌더 전 — 날조는 빌드보다 먼저 잡는다) ----
    g10 = g10_quote_check(book_dir, outline)
    report["gates"]["G10"] = {"problems": g10, "ok": not g10}
    if g10:
        finish(book_dir, report, ["G10: " + p for p in g10])

    # ---- G13 (렌더 전 — AI-tell 지문. strict 모드만 하드, warn은 보고, off는 생략) ----
    slop = lint_book(book_dir)
    g13_fail = slop["mode"] == "strict" and slop["counts"]["fail"] > 0
    report["gates"]["G13"] = {"mode": slop["mode"], "counts": slop["counts"],
                              "findings": slop["findings"][:50], "ok": not g13_fail}
    if g13_fail:
        finish(book_dir, report, [
            f"G13: {f['file']}:{f['line']} [{f['id']}] …{f['excerpt']}…"
            for f in slop["findings"] if f["level"] == "fail"][:10])
    if slop["counts"]["fail"] or slop["counts"]["warn"]:
        msg = (f"G13: slop 지문 fail {slop['counts']['fail']} · warn {slop['counts']['warn']} "
               f"(모드 {slop['mode']} — preview.py 하이라이트로 확인)")
        report["warns"].append(msg)
        print("WARN " + msg)

    pdf = book_dir / "draft" / "book.pdf"

    # ---- G1 render + page count ----
    g1 = {"exists": pdf.exists()}
    if not pdf.exists():
        finish(book_dir, report, ["G1: draft/book.pdf missing"])
    doc = fitz.open(pdf)
    n = doc.page_count
    lo, hi = tokens.get("length_pages", {}).get(book.get("length", "short"), [10, 400])
    in_range = lo <= n <= hi
    g1.update({"pages": n, "range": [lo, hi], "ok": in_range or refit, "refit": refit})
    report["gates"]["G1"] = g1
    if not in_range:
        msg = f"G1: page count {n} outside [{lo},{hi}]"
        if refit:
            report["warns"].append(msg + " (refit: WARN — 쪽수는 조판을 압박하지 않는다)")
        else:
            fails.append(msg)

    # ---- G2 font embedding ----
    not_embedded = set()
    for pno in range(n):
        for f in doc.get_page_fonts(pno, full=True):
            xref, ext, ftype, basefont = f[0], f[1], f[2], f[3]
            if ftype == "Type3":
                continue
            if ext in ("n/a", ""):
                extracted = doc.extract_font(xref)
                if not extracted or not extracted[-1]:
                    not_embedded.add(basefont)
    report["gates"]["G2"] = {"not_embedded": sorted(not_embedded), "ok": not not_embedded}
    if not_embedded:
        fails.append(f"G2: fonts not embedded: {sorted(not_embedded)}")

    # ---- G3 overflow ----
    overflows = []
    for pno in range(n):
        page = doc[pno]
        pr = page.rect
        clip = fitz.Rect(pr.x0 - TOL, pr.y0 - TOL, pr.x1 + TOL, pr.y1 + TOL)
        for block in page.get_text("dict").get("blocks", []):
            bb = fitz.Rect(block["bbox"])
            if not clip.contains(bb):
                overflows.append({"page": pno + 1, "bbox": list(block["bbox"]),
                                  "kind": "text" if block.get("type") == 0 else "image"})
    report["gates"]["G3"] = {"overflows": overflows[:20], "count": len(overflows), "ok": not overflows}
    if overflows:
        fails.append(f"G3: {len(overflows)} bbox overflow(s), first on page {overflows[0]['page']}")

    # ---- G4 TOC / bookmarks ----
    toc_entries = doc.get_toc(simple=True)
    lvl1 = [(t.strip(), p) for (l, t, p) in toc_entries if l == 1]
    g4 = {"bookmarks": len(lvl1), "mismatches": [], "ok": True}
    want = [ch["title"].strip() for ch in outline["chapters"]]
    if len(lvl1) < len(want):
        g4["ok"] = False
        g4["mismatches"].append(f"bookmark count {len(lvl1)} < chapters {len(want)}")
    else:
        for title, page in lvl1:
            if page < 1 or page > n:
                g4["ok"] = False
                g4["mismatches"].append(f"'{title}' -> bad page {page}")
            else:
                text = doc[page - 1].get_text()
                if norm(title) not in norm(text):
                    g4["ok"] = False
                    g4["mismatches"].append(f"'{title}' not found on its page {page}")
    report["gates"]["G4"] = g4
    if not g4["ok"]:
        fails.append("G4: " + "; ".join(g4["mismatches"][:3]))

    page_texts = [doc[i].get_text() for i in range(n)]
    doc.close()

    # ---- 밀도 전처리 (pagination.md §7) ----
    frame_mm = tokens.get("body_frame_mm")
    if not frame_mm:
        finish(book_dir, report, [f"G7: styles/{style}/tokens.json에 body_frame_mm 없음"])
    m = analyze(pdf, frame_mm)
    pages = m["pages"]
    N = m["n_grid"] or 1

    ch_starts = sorted({p for (_, p) in lvl1 if 1 <= p <= n})
    first_ch = ch_starts[0] if ch_starts else 1
    colophon_pages = {i + 1 for i, t in enumerate(page_texts)
                      if "bookforge" in t and "조판" in t and i + 1 >= (ch_starts[-1] if ch_starts else 1)}
    fullbleed = {p["page"] for p in pages if p["imgarea"] >= 0.60}
    # float 밀림 면제(구조 파생): 다음 면 첫 블록(통짜 표·그림)이 이 면 잔여 공간보다 크면
    # 이 면의 미달은 결속 규칙의 정당한 대가다 — 중간면 판정에서 제외.
    float_pushed = set()
    pitch_ref = m["book_pitch"] or 12
    for i, p in enumerate(pages[:-1]):
        nxt = pages[i + 1]
        fl, ft, fr, fb = nxt["frame"]
        # 텍스트는 나눠 흐를 수 있으므로 면제 사유가 못 된다 — 객체(표 괘선·그림·박스)만 본다.
        segs = sorted(nxt["_objs"])
        if not segs or segs[0][0] > ft + 2 * pitch_ref:
            continue  # 다음 면이 객체로 시작하지 않으면 밀림이 아니다
        top0, cur1 = segs[0]
        for a, b in segs[1:]:
            if a - cur1 > pitch_ref * 3:  # 표는 모든 행에 괘선이 있지 않다 — 행 건너뜀 허용
                break
            cur1 = max(cur1, b)
        first_block_h = cur1 - top0
        remaining = (1 - p["reach"]) * (p["frame"][3] - p["frame"][1])
        # 블록은 CSS/Typst 마진을 데리고 다닌다 — 3행송 슬랙 인정
        if first_block_h + pitch_ref * 3 > remaining:
            float_pushed.add(p["page"])
    body_last = max((p["page"] for p in pages
                     if p["lines"] > 0 and p["page"] not in colophon_pages), default=n)
    tails = {p - 1 for p in ch_starts if p - 1 >= first_ch} | {body_last}
    structural = (set(range(1, first_ch)) | set(ch_starts) | colophon_pages | fullbleed)

    # ---- G11 pageroles.json ----
    roles_p = book_dir / "pageroles.json"
    roles = []
    g11 = {"declared": 0, "problems": [], "ok": True}
    if roles_p.exists():
        roles = json.loads(roles_p.read_text(encoding="utf-8")).get("roles", [])
        g11["declared"] = len(roles)
        budget = max(3, int(0.08 * n))
        if len(roles) > budget:
            g11["problems"].append(f"선언 면 {len(roles)} > 예산 {budget}")
        breaths = 0
        for r in roles:
            pg, code = r.get("page"), r.get("code")
            why, anchor = (r.get("why") or "").strip(), r.get("anchor")
            pm = next((p for p in pages if p["page"] == pg), None)
            if code not in ROLE_CODES:
                g11["problems"].append(f"p{pg}: 코드 '{code}' 화이트리스트 밖"); continue
            if not why:
                g11["problems"].append(f"p{pg}: why 비어 있음")
            if pm is None or not (1 <= pg <= n):
                g11["problems"].append(f"p{pg}: 존재하지 않는 면"); continue
            # 기계 선행조건 — 불충족 시 코드 자체가 FAIL (도장 방지)
            if code == "FULL_BLEED_PLATE" and pm["imgarea"] < 0.60:
                g11["problems"].append(f"p{pg}: FULL_BLEED_PLATE인데 imgarea {pm['imgarea']} < 0.60")
            if code == "PART_DIVIDER" and pm["lines"] > 3:
                g11["problems"].append(f"p{pg}: PART_DIVIDER인데 텍스트 {pm['lines']}행 > 3")
            if code == "EXEC_SUMMARY":
                if style != "business" or not (0.35 <= pm["ink"] <= 0.70):
                    g11["problems"].append(f"p{pg}: EXEC_SUMMARY 선행조건 위반 (style={style}, ink={pm['ink']})")
            if code == "ESSAY_BREATH":
                breaths += 1
                if style != "essay" or pg not in tails or pm["lines"] < 6:
                    g11["problems"].append(f"p{pg}: ESSAY_BREATH 선행조건 위반 (꼬리 면·6행 이상·essay 한정)")
            if code == "MAGAZINE_WHITESPACE" and style != "magazine":
                g11["problems"].append(f"p{pg}: MAGAZINE_WHITESPACE는 magazine 한정")
            if code == "TOC_TAIL" and pg >= first_ch:
                g11["problems"].append(f"p{pg}: TOC_TAIL은 본문 시작 전 한정")
            if anchor:
                if norm(anchor) not in norm(page_texts[pg - 1]):
                    g11["problems"].append(f"p{pg}: anchor 불일치(stale — 리빌드로 면 밀림 의심)")
            elif code not in ("FULL_BLEED_PLATE", "PART_DIVIDER"):
                g11["problems"].append(f"p{pg}: anchor 필수(텍스트 면)")
        if breaths > max(1, len(ch_starts)):
            g11["problems"].append(f"ESSAY_BREATH {breaths}회 > 장당 1회 한도")
    g11["ok"] = not g11["problems"]
    report["gates"]["G11"] = g11
    if not g11["ok"]:
        fails.append("G11: " + "; ".join(g11["problems"][:3]))
    role_by_page = {r.get("page"): r.get("code") for r in roles}

    # ---- G7-FRAME 판면 드리프트 ----
    ft_pt = frame_mm[0] * MM2PT
    d_top = m["derived_frame"][0]
    g7f = {"token_top_pt": round(ft_pt, 1), "derived_top_pt": d_top,
           "book_pitch": m["book_pitch"], "n_grid": N, "ok": True}
    if d_top is not None and abs(d_top - ft_pt) > 6:
        g7f["ok"] = False
        fails.append(f"G7-FRAME: tokens 판면 상단 {ft_pt:.1f}pt vs 실측 {d_top}pt — 드리프트 >6pt")
    report["gates"]["G7-FRAME"] = g7f

    # ---- G7-BLANK (구 G5 흡수) + G12 ----
    blanks, parity = [], []
    for p in pages:
        pg = p["page"]
        if p["ink"] >= 0.03 or pg in structural or pg in role_by_page:
            continue
        if pg + 1 in ch_starts:
            parity.append(pg)  # 장 시작 직전 필러 백면 — 인쇄 관습 이식
        else:
            blanks.append(pg)
    report["gates"]["G7-BLANK"] = {"blank_pages": blanks, "ok": not blanks}
    report["gates"]["G5"] = report["gates"]["G7-BLANK"]  # 하위 호환 별칭
    report["gates"]["G12"] = {"parity_filler": parity, "ok": not parity}
    if blanks:
        fails.append(f"G7-BLANK: 의도 없는 백면 {blanks}")
    if parity:
        fails.append(f"G12: 장 시작 직전 필러 백면 {parity} (단면 전자책에 recto 맞춤 금지)")

    # ---- G7-TAIL / G7-MID ----
    tail_hard = TAIL_HARD.get(style, 0.45)
    tail_warn = TAIL_WARN.get(style, 0.70)
    mid_role_min = MID_ROLE_MIN.get(style, 0.90)
    g7t = {"tails": [], "ok": True}
    g7m = {"underfull": [], "ok": True}
    tail_reaches = []
    for p in pages:
        pg = p["page"]
        if pg < first_ch or pg in colophon_pages or pg in fullbleed or pg in ch_starts:
            continue
        code = role_by_page.get(pg)
        if pg in tails:
            tail_reaches.append(p["reach"])
            entry = {"page": pg, "reach": p["reach"], "lines": p["lines"], "role": code}
            g7t["tails"].append(entry)
            if p["lines"] < 6:
                g7t["ok"] = False
                fails.append(f"G7-TAIL: p{pg} 꼬리 {p['lines']}행 < 6 (HARD — 사유 코드 불가)")
            elif p["reach"] < tail_hard:
                g7t["ok"] = False
                fails.append(f"G7-TAIL: p{pg} reach {p['reach']} < HARD {tail_hard}")
            elif p["reach"] < tail_warn and not code:
                if style in WARN_REPORT_ONLY:
                    report["warns"].append(f"G7-TAIL: p{pg} reach {p['reach']} < {tail_warn} (report-only)")
                else:
                    g7t["ok"] = False
                    fails.append(f"G7-TAIL: p{pg} reach {p['reach']} < {tail_warn}, 사유 코드 없음")
        else:
            if pg in float_pushed:
                report["warns"].append(f"G7-MID: p{pg} reach {p['reach']} — float 밀림 면제(다음 면 통짜 블록)")
                continue
            if p["reach"] < MID_HARD:
                g7m["underfull"].append({"page": pg, "reach": p["reach"]})
                g7m["ok"] = False
                fails.append(f"G7-MID: p{pg} reach {p['reach']} < {MID_HARD} (HARD)")
            elif p["reach"] < mid_role_min and not code:
                if style in WARN_REPORT_ONLY:
                    report["warns"].append(f"G7-MID: p{pg} reach {p['reach']} < {mid_role_min} (report-only)")
                else:
                    g7m["underfull"].append({"page": pg, "reach": p["reach"]})
                    g7m["ok"] = False
                    fails.append(f"G7-MID: p{pg} reach {p['reach']} < {mid_role_min}, 사유 코드 없음")
    report["gates"]["G7-TAIL"] = g7t
    report["gates"]["G7-MID"] = g7m

    # ---- G7-DOC 문서 통계 (실측 근거 있는 스타일만) ----
    if style in DOC_STATS_STYLES and tail_reaches:
        med = round(median(tail_reaches), 3)
        p10 = round(sorted(tail_reaches)[max(0, int(0.1 * len(tail_reaches)) - 0)], 3) \
            if len(tail_reaches) >= 3 else min(tail_reaches)
        ok = med >= 0.80 and p10 >= 0.55
        report["gates"]["G7-DOC"] = {"median": med, "p10": p10, "ok": ok}
        if not ok:
            fails.append(f"G7-DOC: 꼬리 reach 중앙값 {med}/p10 {p10} < 0.80/0.55 — 원고 분량 설계 반환")

    # 본문 크기 = 글자 수 가중 최빈값(행 수 기준이면 리스트·표 9pt가 본문 10.5pt를 이길 수 있다)
    sizes = Counter()
    for p in pages:
        for l in p["_lines"]:
            sizes[round(l["size"] * 2) / 2] += len(l["text"])
    body_size = sizes.most_common(1)[0][0] if sizes else 10.0

    # ---- G8-STRETCH 공기 채움 ----
    g8 = {"stretched": [], "ok": True}
    for p in pages:
        pg = p["page"]
        if pg < first_ch or pg in structural or pg in tails or pg in role_by_page \
                or pg in float_pushed:
            continue
        # insight는 H2 위 여백 24mm(STYLE.md 정본)+와이드 콜아웃이 구조적 공기를 만든다 — 임계 완화.
        # 한 면에 섹션 전환이 2회 이상이면 여백이 배로 쌓이므로 디스플레이 행 수 비례 가산.
        gap_thr = 0.18
        if style == "insight":
            heads = sum(1 for l in p["_lines"] if l["size"] >= 1.3 * body_size)
            gap_thr = 0.28 + 0.10 * max(0, heads - 1)
        if p["gap"] > gap_thr and p["lines"] < 0.8 * N:
            g8["stretched"].append({"page": pg, "gap": p["gap"], "lines": p["lines"]})
        # 행송 편차는 WARN만 — 두 엔진 모두 페이지 단위로 행송을 벌릴 능력이 없다(실측).
        # 리스트·코드·콜아웃 혼합 면의 자연 편차가 대부분이라 FAIL로 쓰면 오탐.
        if p["pitch"] and m["book_pitch"] and p["lines"] >= 10 and \
                abs(p["pitch"] - m["book_pitch"]) / m["book_pitch"] > 0.03:
            report["warns"].append(f"G8: p{pg} 행송 편차 {p['pitch']} vs {m['book_pitch']} (혼합 콘텐츠 추정)")
    g8["ok"] = not g8["stretched"]
    report["gates"]["G8"] = g8
    if g8["stretched"]:
        first = g8["stretched"][0]
        fails.append(f"G8-STRETCH: 공기 채움/행송 이탈 {len(g8['stretched'])}면, 첫 면 p{first['page']}")

    # ---- G9-KEEP 제목 고립·widow ----
    g9 = {"violations": [], "ok": True}
    single_col = style in ("practical", "academic", "essay", "business")
    for i, p in enumerate(pages):
        pg = p["page"]
        if pg < first_ch or pg in structural or not p["_lines"]:
            continue
        last = p["_lines"][-1]
        if last["size"] >= 1.3 * body_size and pg not in tails:  # 1.1~1.3 대역은 스탯 라벨·덱 오탐(폰트 판별은 백로그)
            g9["violations"].append(f"p{pg}: 면 끝 제목 고립('{last['text'][:20]}')")
        if single_col and i > 0 and pages[i - 1]["_lines"] and pg - 1 >= first_ch \
                and pg - 1 not in ch_starts:
            fl_, _, fr_, _ = p["frame"]
            first_l, prev_last = p["_lines"][0], pages[i - 1]["_lines"][-1]
            w = fr_ - fl_
            if abs(first_l["size"] - body_size) <= 0.5 and \
                    first_l["x1"] < fr_ - 0.15 * w and prev_last["x1"] >= fr_ - 0.05 * w and \
                    len(p["_lines"]) >= 2 and abs(p["_lines"][1]["size"] - body_size) <= 0.5 and \
                    p["_lines"][1]["y0"] - first_l["y0"] > (m["book_pitch"] or 12) * 1.5:
                g9["violations"].append(f"p{pg}: widow 의심(면 첫 행이 앞 문단 끝줄)")
    g9["ok"] = not g9["violations"]
    report["gates"]["G9"] = g9
    if g9["violations"]:
        fails.append("G9-KEEP: " + "; ".join(g9["violations"][:3]))

    # metrics 요약을 리포트에 (디버그·refit 입력)
    report["metrics"] = {"book_pitch": m["book_pitch"], "n_grid": N,
                         "pages": [{k: p[k] for k in ("page", "lines", "reach", "ink", "gap", "imgarea")}
                                   for p in pages],
                         "chapter_starts": ch_starts, "tails": sorted(tails),
                         "structural_exempt": sorted(structural)}

    if fails:
        finish(book_dir, report, fails)

    report["pass"] = True
    final_dir = book_dir / "final"
    final_dir.mkdir(exist_ok=True)
    dst = final_dir / f"{book_dir.name}.pdf"
    shutil.copy(pdf, dst)
    report["final"] = str(dst)
    (book_dir / "gate-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for w in report["warns"]:
        print("WARN", w)
    print(f"PASS -> {dst}")
    sys.exit(0)


def finish(book_dir, report, fails):
    report["fails"] = fails
    (book_dir / "gate-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for w in report.get("warns", []):
        print("WARN", w, file=sys.stderr)
    for f in fails:
        print("FAIL", f, file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
