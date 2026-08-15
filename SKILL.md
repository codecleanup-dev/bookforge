---
name: bookforge
description: Generate commercial-book-quality Korean ebook PDFs from a topic or a finished manuscript. Six design styles (practical, insight, academic, essay, business, magazine) with real book anatomy — cover, TOC with page numbers, chapter openers, running heads, QC gates. Use when the user wants an ebook, a PDF book or report, book typesetting, or mentions 전자책, PDF 책, 책 조판, 북포지, bookforge.
---

# bookforge — 상업도서급 전자책 PDF 공장

주제 한 줄 또는 완성 원고를 받아, 실제 단행본 해부 구조(표지·차례·장 도비라·러닝 시스템·판권면)를 갖춘 PDF를 만든다. 콘텐츠는 마크다운으로만 쓰고, 조판은 스타일 팩과 스크립트가 전담한다. 품질은 QC 게이트가 물리적으로 강제한다 — 게이트를 통과하지 못한 PDF는 `final/`에 존재할 수 없다.

이 파일의 경로가 `<SKILL>`이다. 모든 명령은 `<SKILL>`을 이 스킬 폴더의 절대 경로로 치환해 실행한다.

## 실행 전 점검

```bash
typst --version        # 0.14+ 필요 (Typst 트랙)
python3 -c "import fitz, markdown_it"   # PyMuPDF + markdown-it-py (QC·변환)
npx --no-install playwright --version   # HTML 트랙(insight·magazine)에만 필요
```

셋 중 없는 것이 있으면 사용자에게 설치를 요청하고 중단한다. HTML 트랙 없이 Typst 4스타일만 쓸 거라면 playwright는 생략 가능.

## 파이프라인 (체크리스트를 복사해 진행하며 체크)

```
[ ] P0 계약: 모드·스타일·분량 확정 → 책 프로젝트 스캐폴드
[ ] P1 콘텐츠: outline.json + chapters/ch-NN.md 완성
[ ] P1.5 원고 검수: preview.py → preview/manuscript.html 확인 (내용·문장·slop) — 조판 전 원고 확정
[ ] P2-3 빌드: build.py → draft/book.pdf
[ ] P4 게이트: qc_gate.py PASS → final/ 생성 확인
[ ] P5 시각 검수: contact_sheet.py → 표지·차례·도비라·본문 4면 이상 눈으로 확인
```

## P0 — 계약

1. **모드 감지**: 사용자가 원고 파일(md/txt/docx)을 줬으면 manuscript 모드 → [modes/manuscript.md](modes/manuscript.md)를 읽고 따른다. 주제·아이디어만 줬으면 topic 모드 → [modes/topic.md](modes/topic.md)를 읽고 따른다.
2. **스타일 선택**: 사용자가 지정하지 않았으면 아래 표에서 내용 성격에 맞는 것을 골라 진행한다(질문하지 않는다).

| 스타일 | 성격 | 판형 | 엔진 |
|---|---|---|---|
| `practical` | IT·실용 활용서, 단계별 가이드, 용어집 | 153×225 | typst |
| `insight` | 기술 동향·인사이트 리포트, 데이터 브리핑 | 182×257 | html |
| `academic` | 학술 단행본, 연구 개론, 이론서 | 153×225 | typst |
| `essay` | 산문집, 회고, 문학적 글 | 128×188 | typst |
| `business` | 컨설팅 리포트, 시장 분석, 전략 백서 | 200×280 | typst |
| `magazine` | 트렌드북, 큐레이션, 룩북 | 200×265 | html |

3. **스캐폴드** (책 프로젝트는 스킬 폴더 밖 작업 디렉토리에 만든다):

```bash
python3 <SKILL>/scripts/scaffold.py <book_dir> --style practical \
  --title "제목" --subtitle "부제" --length short --author "저자" --date "2026-08"
```

`--length`: short(30~60쪽)/standard(80~150)/long. `--brand "#hex"`로 브랜드색 교체 가능. book.json의 `images`는 이미지 정책(vector=벡터만·generated=생성 아트 포함·none)이다.

## P1 — 콘텐츠 계약

`outline.json`의 각 장에 `file`·`title`·`summary`(도비라에 실리는 1~2문장)를 채우고, `chapters/ch-NN.md`를 아래 문법만으로 쓴다:

- `# 장제목`(파일당 1개, outline의 title과 일치) / `##` 절 / `###` 소제목
- 문단, `**볼드**`, 리스트, `> 인용`, GFM 표, ``` 코드블록
- 이미지: `![캡션](assets/파일.png "출처: 어디")` — 파일을 `<book_dir>/assets/`에 먼저 넣는다
- 콜아웃(줄 단위 디렉티브):

```
::: tip 제목
내용 (stat은 첫 줄=수치, 둘째 줄=설명)
:::
```

종류 `info|tip|warn|quote|stat|pull`(pull은 magazine 풀퀘트 — **본문에 실재하는 문장만**, 없는 인용은 G10 하드 실패).
- 표 캡션: 표 **바로 앞 문단**에 `[표] 제목 | 자료: 출처` 한 줄 — 이 줄을 준 표만 번호 라벨이 붙는다. 캡션 없는 표는 라벨 없이 렌더된다(자동 필러 캡션은 존재하지 않는다).
- `stat`의 수치는 같은 장 본문에 실재해야 한다(G10) — 박스에만 있는 숫자는 날조로 판정된다.

표지·도비라용 생성 아트를 쓸 경우 [references/art-policy.md](references/art-policy.md)를 읽고 따른다(무텍스트 원칙).

완료 기준: outline의 모든 장 파일이 존재하고, 각 파일 첫 줄이 `# {title}`이며, 분량 프리셋에 맞는 총 글자수(short 기준 본문 1.2만~2.2만 자)를 갖춘다.

## P1.5 — 원고 검수 (조판 전, 생략 금지)

```bash
python3 <SKILL>/scripts/preview.py <book_dir>     # → preview/manuscript.html (slop 검사 포함)
python3 <SKILL>/scripts/slop_lint.py <book_dir>   # 지문 검사만 따로 돌릴 때
```

`preview/manuscript.html`을 브라우저로 열어 내용과 문장을 확정하는 단계다. 연속 스크롤이라 페이지 분할은 없고, 장별 글자수와 slop 지문(하이라이트)이 함께 표시된다. **조판 후 문장 수정은 표 높이·페이지 분할을 되돌려 게이트 라운드를 재유발한다 — 문장 품질은 여기서 끝낸다.**

- slop 지문 정의: `styles/slop-patterns.json`(기본) 또는 `<book_dir>/slop-patterns.json`(통째 오버라이드). fail 레벨(엠대시 등)은 재서술 필수, warn 레벨(슬래시 접합 등)은 정당한 데이터 나열인지 사람이 판단한다.
- 게이트 모드: book.json `"slop_lint"` = `strict`(fail 지문 = G13 하드 실패, 신규 스캐폴드 기본) / `warn`(보고만 — 키 없는 기존 책 기본) / `off`.
- 사용자 패턴은 기본 10초 안에 검사를 끝내야 한다. 격리된 신뢰 환경에서만 `SLOP_LINT_TIMEOUT=0`으로 timeout을 끌 수 있다.
- HTML 트랙(insight·magazine)은 빌드 후 `typeset/book.html`을 열고 **브라우저 인쇄 미리보기**로 페이지 분할까지 사전 확인할 수 있다 — 인쇄 엔진이 곧 PDF 엔진이다.

## P2-4 — 빌드와 게이트

```bash
python3 <SKILL>/scripts/build.py <book_dir>          # → draft/book.pdf
python3 <SKILL>/scripts/qc_gate.py <book_dir>        # PASS 시에만 final/<slug>.pdf 생성
```

게이트: G10 인용·수치 실재(렌더 전) / G13 AI-tell 지문(렌더 전, strict 모드만 하드) / G1 렌더·분량범위 / G2 폰트 임베드 / G3 오버플로 0 / G4 목차·북마크 정합 / G7 밀도(백면·꼬리 채움·판면 드리프트) / G8 공기 채움 / G9 제목 고립·widow / G11 사유 코드 무결성 / G12 필러 백면. 기준 수치와 대응법은 [references/pagination.md](references/pagination.md)가 정본이다.

실패 시 `gate-report.json`의 원인 항목만 고치고 재실행한다. **금지 대응**: 분량 미달을 부록·용어집 추가로 메우기, 절별 강제 개면, 빈 줄·행간 확대로 면 채우기 — 전부 게이트가 다시 잡는다. 올바른 대응: G7 꼬리 미달은 `refit.py`(자간 미세조정 자동 탐색) → 해 없으면 문단 1~2개 국소 증감 또는 `pageroles.json` 사유 코드(의도된 여백 선언, G11이 진위 검증). 재배치(원고 유지) 시에는 `qc_gate.py <dir> --refit`으로 분량범위를 WARN 강등. 같은 게이트 3회 연속 실패면 원인을 사용자에게 보고한다.

## P5 — 시각 검수 (필수, 생략 금지)

```bash
python3 <SKILL>/scripts/contact_sheet.py <book_dir>/final/*.pdf <book_dir>/qc --dpi 90 --pages 1,2,3,4,5
# 게이트 실패를 진단할 때는 final/ 대신 draft/book.pdf 를 같은 방식으로 렌더해 본다
```

표지·차례·도비라·본문 펼침면 PNG를 직접 열어 보고 판단한다: 글자 겹침 없음, 목차 쪽번호=실제 쪽, 도비라 스타일 성립, 본문 여백 리듬 정상. 이상이 있으면 콘텐츠(md)나 book.json을 고쳐 P2-4를 재실행한다. **파일이 생성되었다는 것은 완료가 아니다 — 눈으로 본 것만 완료다.**

## 더 읽을 것 (필요할 때만)

- [modes/topic.md](modes/topic.md) — 주제만 받았을 때: 조사→목차→집필 절차
- [modes/manuscript.md](modes/manuscript.md) — 원고를 받았을 때: 인제스트·장 분할 절차
- [references/pagination.md](references/pagination.md) — **배치 규칙서(정본)**: 채움/비움의 사유, 레버 사다리, 밀도 게이트 수치, 사유 코드
- [references/art-policy.md](references/art-policy.md) — 생성 아트(표지·도비라) 규칙: 무텍스트 원칙, 스타일별 사용처
- [references/orchestration.md](references/orchestration.md) — (Claude Code 전용, 선택) 장별 집필을 codex 스웜·서브에이전트로 병렬화하는 법
- [references/extending.md](references/extending.md) — 새 스타일 팩 추가·테마 수정 가이드
- `styles/<이름>/STYLE.md` — 각 스타일의 전체 디자인 규칙서(집필 시 톤·구성 참고)
