#!/usr/bin/env python3
"""bookforge slop lint — 챕터 md의 AI-tell 지문(기계 문체 흔적) 검사. G13 판정 엔진.

Usage:
    python3 slop_lint.py <book_dir> [--json]

패턴 정의: <SKILL>/styles/slop-patterns.json 이 기본이고,
<book_dir>/slop-patterns.json 이 있으면 그 파일이 통째로 우선한다(병합 아님 — 판정 재현성).

모드: book.json "slop_lint" = "strict" | "warn" | "off" (없으면 "warn").
  strict — fail 레벨 지문 발견 시 exit 2 (qc_gate G13 하드 실패와 동일 판정)
  warn   — 보고만 하고 exit 0
  off    — 검사 생략

검사 범위: outline.json에 등록된 chapters/*.md 본문. 코드 펜스와 인라인 코드는 제외한다.
"""
from contextlib import contextmanager
import json
import math
import multiprocessing
import os
import re
import signal
import sys
import threading
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.rules_inline.autolink import autolink as parse_autolink
from markdown_it.rules_inline.link import link as parse_link
from markdown_it.rules_inline.state_inline import StateInline

SKILL = Path(__file__).resolve().parent.parent
MARKDOWN = MarkdownIt("commonmark", {"html": False})

INLINE_TICKS = re.compile(r"`+")

VALID_MODES = {"strict", "warn", "off"}
VALID_LEVELS = {"fail", "warn"}
MAX_FINDINGS = 10_000


class SlopLintError(ValueError):
    """설정 또는 검사 계약 위반. 호출자는 반드시 fail-closed 처리해야 한다."""


class _ScanTimeout(TimeoutError):
    pass


def _read_json(path: Path, label: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SlopLintError(f"{label}: 파일이 없습니다 ({path})") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SlopLintError(f"{label}: 읽을 수 없는 JSON입니다 ({path}): {exc}") from exc


def load_patterns(book_dir: Path):
    """패턴 정의를 검증해 로드한다. 오타·빈 설정은 strict 우회이므로 거부한다."""
    candidates = (book_dir / "slop-patterns.json", SKILL / "styles" / "slop-patterns.json")
    for cand in candidates:
        if not cand.exists() and not cand.is_symlink():
            continue
        if cand.is_symlink() or not cand.is_file():
            raise SlopLintError(f"slop-patterns: 심볼릭 링크/비일반 파일은 허용하지 않습니다 ({cand})")
        data = _read_json(cand, "slop-patterns")
        if not isinstance(data, dict) or not isinstance(data.get("patterns"), list):
            raise SlopLintError(f"slop-patterns: 최상위 patterns 배열이 필요합니다 ({cand})")
        if not data["patterns"]:
            raise SlopLintError(f"slop-patterns: patterns 배열이 비어 있습니다 — off 모드를 사용하세요 ({cand})")

        pats = []
        seen_ids = set()
        for idx, item in enumerate(data["patterns"], 1):
            if not isinstance(item, dict):
                raise SlopLintError(f"slop-patterns: patterns[{idx}]는 객체여야 합니다 ({cand})")
            pattern_id = item.get("id")
            regex = item.get("regex")
            level = item.get("level", "warn")
            desc = item.get("desc", "")
            if not isinstance(pattern_id, str) or not pattern_id.strip():
                raise SlopLintError(f"slop-patterns: patterns[{idx}].id는 비어 있지 않은 문자열이어야 합니다 ({cand})")
            if pattern_id in seen_ids:
                raise SlopLintError(f"slop-patterns: 중복 id '{pattern_id}' ({cand})")
            if not isinstance(regex, str) or not regex:
                raise SlopLintError(f"slop-patterns: regex는 비어 있지 않은 문자열이어야 합니다 (id={pattern_id}, {cand})")
            if level not in VALID_LEVELS:
                raise SlopLintError(f"slop-patterns: 알 수 없는 level '{level}' (id={pattern_id}) — "
                                    f"{sorted(VALID_LEVELS)} 중 하나여야 합니다 ({cand})")
            if not isinstance(desc, str):
                raise SlopLintError(f"slop-patterns: desc는 문자열이어야 합니다 (id={pattern_id}, {cand})")
            try:
                rx = re.compile(regex)
            except re.error as exc:
                raise SlopLintError(f"slop-patterns: 잘못된 정규식 (id={pattern_id}): {exc} ({cand})") from exc
            if rx.search("") is not None:
                raise SlopLintError(f"slop-patterns: 빈 문자열과 일치하는 정규식은 허용하지 않습니다 "
                                    f"(id={pattern_id}, {cand})")
            seen_ids.add(pattern_id)
            pats.append({"id": pattern_id, "regex": regex, "re": rx,
                         "level": level, "desc": desc})
        return pats, str(cand)
    raise SlopLintError("slop-patterns: 기본/책별 패턴 파일을 찾을 수 없습니다")


def resolve_chapter_files(book_dir: Path, outline: dict | None = None) -> list[tuple[str, Path]]:
    """outline에 등록된, chapters/ 바로 아래의 실제 .md 파일만 반환한다."""
    book_dir = Path(book_dir)
    chapters_dir = book_dir / "chapters"
    if chapters_dir.is_symlink() or not chapters_dir.is_dir():
        raise SlopLintError("chapters/: 심볼릭 링크가 아닌 실제 디렉토리여야 합니다")
    real_chapters_dir = chapters_dir.resolve()

    if outline is None:
        outline = _read_json(book_dir / "outline.json", "outline.json")
    if not isinstance(outline, dict) or not isinstance(outline.get("chapters"), list):
        raise SlopLintError("outline.json: 최상위 chapters 배열이 필요합니다")
    if not outline["chapters"]:
        raise SlopLintError("outline.json: chapters 배열이 비어 있습니다")

    resolved = []
    seen_names = set()
    for idx, chapter in enumerate(outline["chapters"], 1):
        if not isinstance(chapter, dict):
            raise SlopLintError(f"outline.json: chapters[{idx}]는 객체여야 합니다")
        name = chapter.get("file")
        if (not isinstance(name, str) or not name or "/" in name or "\\" in name
                or Path(name).name != name or Path(name).suffix.lower() != ".md"):
            raise SlopLintError(f"outline.json: 잘못된 chapter file '{name}' — "
                                "chapters/ 바로 아래의 .md 파일명만 허용")
        if name in seen_names:
            raise SlopLintError(f"outline.json: 중복 chapter file '{name}'")

        src = chapters_dir / name
        if src.is_symlink() or not src.is_file():
            raise SlopLintError(f"chapters/{name}: 심볼릭 링크가 아닌 실제 파일이어야 합니다")
        try:
            if src.resolve().parent != real_chapters_dir:
                raise SlopLintError(f"chapters/{name}: chapters/ 밖의 파일은 허용하지 않습니다")
        except OSError as exc:
            raise SlopLintError(f"chapters/{name}: 경로를 확인할 수 없습니다: {exc}") from exc
        seen_names.add(name)
        resolved.append((name, src))
    return resolved


def _is_escaped(text: str, pos: int) -> bool:
    backslashes = 0
    pos -= 1
    while pos >= 0 and text[pos] == "\\":
        backslashes += 1
        pos -= 1
    return backslashes % 2 == 1


def _mask_markdown(text: str) -> str:
    """코드와 링크 목적지를 같은 길이의 공백으로 바꾼다. 줄바꿈·열 위치는 보존한다."""
    chars = list(text)
    excluded = [False] * len(text)

    def mask(start: int, end: int):
        for pos in range(start, end):
            excluded[pos] = True
            if chars[pos] not in "\r\n":
                chars[pos] = " "

    # 같은 CommonMark 파서가 인정한 코드 블록과 reference definition을 제외한다.
    # token.map을 쓰면 최상위뿐 아니라 인용·목록 fence와 들여쓰기 코드도 같은
    # 렌더 계약으로 처리되어 닫는 fence 오인이 이후 산문을 가리는 일을 막는다.
    markdown_env = {}
    block_tokens = MARKDOWN.parse(text, markdown_env)
    lines = text.splitlines(keepends=True)
    line_offsets = [0]
    for line in lines:
        line_offsets.append(line_offsets[-1] + len(line))
    for token in block_tokens:
        if token.type in {"fence", "code_block"} and token.map:
            start_line, end_line = token.map
            mask(line_offsets[start_line], line_offsets[min(end_line, len(lines))])
    references = list(markdown_env.get("references", {}).values())
    references.extend(markdown_env.get("duplicate_refs", []))
    for reference in references:
        start_line, end_line = reference["map"]
        mask(line_offsets[start_line], line_offsets[end_line])
    inline_ranges = sorted({tuple(token.map) for token in block_tokens
                            if token.type == "inline" and token.map})
    inline_offsets = [(line_offsets[start_line], line_offsets[end_line])
                      for start_line, end_line in inline_ranges]

    # 실제 inline link로 파싱된 괄호 안(destination·title·공백)만 제외한다.
    # 라벨은 보이는 산문이므로 남겨 G13 검사와 preview 하이라이트를 유지한다.
    for block_start, block_end in inline_offsets:
        for pos in range(block_start, block_end):
            if text[pos] != "[" or excluded[pos] or _is_escaped(text, pos):
                continue
            state = StateInline(text, MARKDOWN, markdown_env, [])
            state.pos, state.posMax = pos, block_end
            if not parse_link(state, True):
                continue
            label_end = MARKDOWN.helpers.parseLinkLabel(state, pos, True)
            open_paren = label_end + 1
            if (label_end >= 0 and open_paren < block_end and text[open_paren] == "("
                    and state.pos > open_paren + 1 and text[state.pos - 1] == ")"):
                mask(open_paren + 1, state.pos - 1)

    # CommonMark autolink는 URL 메타데이터 자체이므로 전체를 제외한다.
    for block_start, block_end in inline_offsets:
        for pos in range(block_start, block_end):
            if text[pos] != "<" or excluded[pos] or _is_escaped(text, pos):
                continue
            state = StateInline(text, MARKDOWN, markdown_env, [])
            state.pos, state.posMax = pos, block_end
            if parse_autolink(state, True):
                mask(pos, state.pos)

    # Code span은 CommonMark inline block 안에서만 닫힌다(빈 줄/다른 문단을 넘지 않음).
    # opener 앞 backslash는 escape지만 code 내부의 backslash는 closer를 escape하지 않는다.
    for start_line, end_line in inline_ranges:
        start, end = line_offsets[start_line], line_offsets[end_line]
        runs = list(INLINE_TICKS.finditer(text, start, end))
        i = 0
        while i < len(runs):
            opener = runs[i]
            if (any(excluded[opener.start():opener.end()])
                    or _is_escaped(text, opener.start())):
                i += 1
                continue
            width = len(opener.group(0))
            closing = next((j for j in range(i + 1, len(runs))
                            if not any(excluded[runs[j].start():runs[j].end()])
                            and len(runs[j].group(0)) == width), None)
            if closing is None:
                i += 1
                continue
            mask(opener.start(), runs[closing].end())
            i = closing + 1

    return "".join(chars)


def _timeout_seconds() -> float:
    raw = os.environ.get("SLOP_LINT_TIMEOUT", "10")
    try:
        value = float(raw)
    except ValueError as exc:
        raise SlopLintError(f"SLOP_LINT_TIMEOUT은 0 이상의 숫자여야 합니다: '{raw}'") from exc
    if not math.isfinite(value) or value < 0:
        raise SlopLintError(f"SLOP_LINT_TIMEOUT은 유한한 0 이상의 숫자여야 합니다: '{raw}'")
    return value


@contextmanager
def _scan_deadline(seconds: float):
    """POSIX 메인 스레드 fast path. 호출 전에 사용 가능 여부를 확인한다."""
    previous_handler = signal.getsignal(signal.SIGALRM)

    def _timeout(_signum, _frame):
        raise _ScanTimeout

    try:
        signal.signal(signal.SIGALRM, _timeout)
        signal.setitimer(signal.ITIMER_REAL, seconds)
    except (OSError, OverflowError, ValueError) as exc:
        signal.signal(signal.SIGALRM, previous_handler)
        raise SlopLintError(f"slop-lint timeout을 설정할 수 없습니다: {exc}") from exc
    try:
        yield
    except _ScanTimeout as exc:
        raise SlopLintError(f"검사 {seconds:g}s 초과 — slop-patterns.json 정규식의 "
                            "파국적 백트래킹(ReDoS) 가능성. 패턴을 점검하세요") from exc
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _scan_chapters(chapters: list[tuple[str, Path]], patterns: list[dict]) -> list[dict]:
    findings = []
    for name, md in chapters:
        try:
            raw = md.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise SlopLintError(f"chapters/{name}: 읽을 수 없습니다: {exc}") from exc
        masked = _mask_markdown(raw)
        for lineno, (line, scan_line) in enumerate(
                zip(raw.split("\n"), masked.split("\n"), strict=True), 1):
            for pattern in patterns:
                for match in pattern["re"].finditer(scan_line):
                    if match.start() == match.end():
                        raise SlopLintError(
                            f"slop-patterns: 0자 길이 매치는 허용하지 않습니다 "
                            f"(id={pattern['id']}, chapters/{name}:{lineno})")
                    lo, hi = max(0, match.start() - 18), min(len(line), match.end() + 18)
                    findings.append({
                        "file": name, "line": lineno, "col": match.start() + 1,
                        "id": pattern["id"], "level": pattern["level"],
                        "desc": pattern["desc"], "match": line[match.start():match.end()],
                        "excerpt": line[lo:hi].strip(),
                    })
                    if len(findings) > MAX_FINDINGS:
                        raise SlopLintError(f"지문이 {MAX_FINDINGS:,}건을 초과했습니다 — "
                                            "패턴 범위를 좁히세요")
    return findings


def _scan_worker(send_conn, chapters, pattern_specs):
    """SIGALRM이 없는 플랫폼/스레드용 격리 worker."""
    try:
        patterns = [{**spec, "re": re.compile(spec["regex"])} for spec in pattern_specs]
        send_conn.send(("ok", _scan_chapters(chapters, patterns)))
    except Exception as exc:  # 자식 traceback 대신 부모의 fail-closed 진단으로 정규화
        send_conn.send(("error", str(exc)))
    finally:
        send_conn.close()


def _scan_in_worker(chapters, patterns, seconds: float):
    context = multiprocessing.get_context("spawn")
    recv_conn, send_conn = context.Pipe(duplex=False)
    specs = [{key: value for key, value in pattern.items() if key != "re"}
             for pattern in patterns]
    process = context.Process(target=_scan_worker, args=(send_conn, chapters, specs))
    started = False
    try:
        process.start()
        started = True
        send_conn.close()
        if not recv_conn.poll(seconds):
            process.terminate()
            process.join(timeout=1)
            raise SlopLintError(f"검사 {seconds:g}s 초과 — slop-patterns.json 정규식의 "
                                "파국적 백트래킹(ReDoS) 가능성. 패턴을 점검하세요")
        try:
            status, payload = recv_conn.recv()
        except EOFError as exc:
            raise SlopLintError("slop-lint 격리 worker가 결과 없이 종료했습니다") from exc
        process.join(timeout=1)
        if process.is_alive():
            process.terminate()
            process.join(timeout=1)
        if status != "ok":
            raise SlopLintError(payload)
        return payload
    except (OSError, OverflowError, RuntimeError, ValueError) as exc:
        raise SlopLintError(f"slop-lint 격리 worker를 시작할 수 없습니다: {exc}") from exc
    finally:
        send_conn.close()
        recv_conn.close()
        if started and process.is_alive():
            process.terminate()
            process.join(timeout=1)


def _scan_with_timeout(chapters, patterns, seconds: float):
    if seconds == 0:
        return _scan_chapters(chapters, patterns)
    required = ("SIGALRM", "ITIMER_REAL", "getitimer", "setitimer")
    signal_ready = all(hasattr(signal, name) for name in required)
    if signal_ready and threading.current_thread() is threading.main_thread():
        previous_timer = signal.getitimer(signal.ITIMER_REAL)
        if previous_timer[0] == 0 and previous_timer[1] == 0:
            with _scan_deadline(seconds):
                return _scan_chapters(chapters, patterns)
    return _scan_in_worker(chapters, patterns, seconds)


def lint_book(book_dir: Path) -> dict:
    book_dir = Path(book_dir)
    book = _read_json(book_dir / "book.json", "book.json")
    if not isinstance(book, dict):
        raise SlopLintError("book.json: 최상위 값은 객체여야 합니다")
    mode = book.get("slop_lint", "warn")
    if mode not in VALID_MODES:
        raise SlopLintError(f"book.json slop_lint: 알 수 없는 모드 '{mode}' — "
                            f"{sorted(VALID_MODES)} 중 하나여야 합니다")
    if mode == "off":
        return {"mode": mode, "patterns_from": None, "findings": [],
                "counts": {"fail": 0, "warn": 0}}

    patterns, source = load_patterns(book_dir)
    chapters = resolve_chapter_files(book_dir)
    findings = _scan_with_timeout(chapters, patterns, _timeout_seconds())
    counts = {"fail": sum(1 for finding in findings if finding["level"] == "fail"),
              "warn": sum(1 for finding in findings if finding["level"] == "warn")}
    return {"mode": mode, "patterns_from": source, "findings": findings, "counts": counts}


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: python3 scripts/slop_lint.py <book_dir> [--json]")
    try:
        result = lint_book(Path(sys.argv[1]).resolve())
    except SlopLintError as exc:
        sys.exit(f"slop-lint: {exc} (fail-closed)")
    if "--json" in sys.argv[2:]:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for finding in result["findings"]:
            print(f"{finding['level'].upper():4s} {finding['file']}:{finding['line']}:"
                  f"{finding['col']} [{finding['id']}] …{finding['excerpt']}…")
        print(f"slop-lint: mode={result['mode']} fail={result['counts']['fail']} "
              f"warn={result['counts']['warn']}")
    if result["mode"] == "strict" and result["counts"]["fail"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
