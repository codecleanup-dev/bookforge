import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_html import md_to_html  # noqa: E402
from qc_gate import resolve_style_tokens  # noqa: E402
from slop_lint import (SlopLintError, _scan_in_worker, lint_book,  # noqa: E402
                       load_patterns, resolve_chapter_files)


class BookFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="bookforge-test-")
        self.root = Path(self.tempdir.name)
        self.book = self.root / "book"
        (self.book / "chapters").mkdir(parents=True)
        self.env_patch = mock.patch.dict(os.environ, {"SLOP_LINT_TIMEOUT": "0"})
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.tempdir.cleanup()

    def write_book(self, chapter="# 테스트\n\n본문입니다.\n", *, mode="strict", filename="ch-01.md"):
        (self.book / "book.json").write_text(json.dumps({
            "title": "테스트", "style": "insight", "length": "short",
            "slop_lint": mode,
        }), encoding="utf-8")
        (self.book / "outline.json").write_text(json.dumps({
            "chapters": [{"file": filename, "title": "테스트", "summary": "요약"}],
        }), encoding="utf-8")
        (self.book / "chapters" / filename).write_text(chapter, encoding="utf-8")

    def run_script(self, name, *, timeout=10, extra_env=None):
        env = os.environ.copy()
        env["SLOP_LINT_TIMEOUT"] = "0"
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, str(SCRIPTS / name), str(self.book)],
            capture_output=True, text=True, timeout=timeout, env=env,
        )


class SlopLintTests(BookFixture):
    def test_commonmark_fences_and_multi_backtick_inline_code_are_excluded(self):
        self.write_book(
            "# 테스트\n\n"
            "````python\n"
            "코드 안 — 제외\n"
            "~~~\n"
            "```\n"
            "```` trailing\n"
            "아직 코드 — 제외\n"
            "````\n"
            "본문 — 검출\n"
            "``인라인 — 제외``\n"
        )

        result = lint_book(self.book)

        self.assertEqual(1, result["counts"]["fail"])
        self.assertEqual("본문 — 검출", result["findings"][0]["excerpt"])

    def test_commonmark_container_and_indented_code_are_excluded(self):
        self.write_book(
            "# 테스트\n\n"
            "    들여쓰기 코드 — 제외\n\n"
            "> ~~~\n"
            "> 인용 코드 — 제외\n"
            "> ~~~\n\n"
            "- ~~~\n"
            "  목록 코드 — 제외\n"
            "  ~~~\n\n"
            "본문 — 검출\n"
        )

        result = lint_book(self.book)

        self.assertEqual(1, result["counts"]["fail"])
        self.assertEqual("본문 — 검출", result["findings"][0]["excerpt"])

    def test_multiline_code_span_is_excluded_and_escaped_backticks_are_prose(self):
        self.write_book(
            "# 테스트\n\n"
            "`여러 줄 코드 시작\n"
            "코드 안 — 제외`\n\n"
            "\\`일반 — 문장\\`\n"
        )

        result = lint_book(self.book)

        self.assertEqual(1, result["counts"]["fail"])
        self.assertIn("일반 — 문장", result["findings"][0]["excerpt"])

    def test_code_span_closer_backslash_and_paragraph_boundaries_match_commonmark(self):
        self.write_book(
            "# 테스트\n\n"
            "`코드 \\` — 보이는 문장`\n\n"
            "`닫히지 않은 코드\n\n"
            "보이는 — 문장`\n"
        )

        result = lint_book(self.book)

        self.assertEqual(2, result["counts"]["fail"])
        self.assertEqual([3, 7], [finding["line"] for finding in result["findings"]])

    def test_modes_preserve_warn_default_and_off_contract(self):
        self.write_book("# 테스트\n\n본문 — 검출\n", mode="warn")
        self.assertEqual(1, lint_book(self.book)["counts"]["fail"])

        book = json.loads((self.book / "book.json").read_text(encoding="utf-8"))
        book["slop_lint"] = "off"
        (self.book / "book.json").write_text(json.dumps(book), encoding="utf-8")
        (self.book / "slop-patterns.json").write_text("not json", encoding="utf-8")
        self.assertEqual({"fail": 0, "warn": 0}, lint_book(self.book)["counts"])

        del book["slop_lint"]
        (self.book / "book.json").write_text(json.dumps(book), encoding="utf-8")
        (self.book / "slop-patterns.json").unlink()
        self.assertEqual("warn", lint_book(self.book)["mode"])

    def test_malformed_or_empty_pattern_schema_fails_closed(self):
        self.write_book()
        for payload in ({"pattern": []}, {"patterns": []},
                        {"patterns": [{"id": "x", "regex": "", "level": "fail"}]},
                        {"patterns": [{"id": "x", "regex": "(", "level": "fail"}]}):
            with self.subTest(payload=payload):
                (self.book / "slop-patterns.json").write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(SlopLintError):
                    lint_book(self.book)

    def test_zero_width_pattern_fails_closed(self):
        self.write_book("# 테스트\n\na\n")
        (self.book / "slop-patterns.json").write_text(json.dumps({
            "patterns": [{"id": "zero", "regex": "(?=a)", "level": "fail"}],
        }), encoding="utf-8")

        with self.assertRaisesRegex(SlopLintError, "0자 길이"):
            lint_book(self.book)

    def test_pattern_override_symlink_fails_closed(self):
        self.write_book()
        outside = self.root / "outside-patterns.json"
        outside.write_text(json.dumps({
            "patterns": [{"id": "all", "regex": ".+", "level": "fail"}],
        }), encoding="utf-8")
        try:
            (self.book / "slop-patterns.json").symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")

        with self.assertRaises(SlopLintError):
            lint_book(self.book)

    def test_chapter_and_chapters_directory_symlinks_fail_closed(self):
        self.write_book()
        chapter = self.book / "chapters" / "ch-01.md"
        outside = self.root / "outside.md"
        outside.write_text("비밀 — 내용", encoding="utf-8")
        chapter.unlink()
        try:
            chapter.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        with self.assertRaises(SlopLintError):
            lint_book(self.book)

        chapter.unlink()
        chapters = self.book / "chapters"
        chapters.rmdir()
        external_dir = self.root / "external-chapters"
        external_dir.mkdir()
        (external_dir / "ch-01.md").write_text("비밀 — 내용", encoding="utf-8")
        chapters.symlink_to(external_dir, target_is_directory=True)
        with self.assertRaises(SlopLintError):
            resolve_chapter_files(self.book)

    def test_outline_path_escape_fails_closed(self):
        self.write_book()
        (self.book / "outline.json").write_text(json.dumps({
            "chapters": [{"file": "../outside.md", "title": "x", "summary": "x"}],
        }), encoding="utf-8")
        with self.assertRaises(SlopLintError):
            lint_book(self.book)

    def test_only_valid_reference_definitions_are_excluded(self):
        self.write_book(
            "# 테스트\n\n"
            "[valid]: https://example.test/a—b\n"
            "[invalid]: <unterminated — 검출\n"
        )

        result = lint_book(self.book)

        self.assertEqual(1, result["counts"]["fail"])
        self.assertIn("unterminated — 검출", result["findings"][0]["excerpt"])

    def test_duplicate_reference_definitions_are_also_non_rendered_metadata(self):
        self.write_book(
            "# 테스트\n\n"
            "[a]: https://one.example/a—b\n"
            "[a]: https://two.example/c—d\n\n"
            "[a]\n"
        )

        result = lint_book(self.book)

        self.assertEqual(0, result["counts"]["fail"])

    def test_redos_pattern_hits_deadline(self):
        self.write_book("# 테스트\n\n" + "a" * 40 + "b\n", mode="warn")
        (self.book / "slop-patterns.json").write_text(json.dumps({
            "patterns": [{"id": "redos", "regex": "^(a+)+$", "level": "fail"}],
        }), encoding="utf-8")
        started = time.monotonic()
        result = self.run_script(
            "slop_lint.py", timeout=5, extra_env={"SLOP_LINT_TIMEOUT": "0.05"})

        self.assertNotEqual(0, result.returncode)
        self.assertLess(time.monotonic() - started, 3)
        self.assertIn("fail-closed", result.stderr + result.stdout)

    def test_non_finite_timeout_values_fail_closed_without_traceback(self):
        self.write_book()
        for value in ("nan", "inf", "-inf"):
            with self.subTest(value=value):
                result = self.run_script(
                    "slop_lint.py", extra_env={"SLOP_LINT_TIMEOUT": value})
                self.assertEqual(1, result.returncode)
                self.assertIn("fail-closed", result.stderr + result.stdout)
                self.assertNotIn("Traceback", result.stderr + result.stdout)

    def test_spawn_worker_fallback_route_scans_and_times_out(self):
        self.write_book("# 테스트\n\n본문 — 검출\n", mode="warn")
        with mock.patch("slop_lint.threading.current_thread", return_value=object()), \
                mock.patch.dict(os.environ, {"SLOP_LINT_TIMEOUT": "3"}):
            result = lint_book(self.book)
        self.assertEqual(1, result["counts"]["fail"])

        self.write_book("# 테스트\n\n" + "a" * 40 + "b\n", mode="warn")
        (self.book / "slop-patterns.json").write_text(json.dumps({
            "patterns": [{"id": "redos", "regex": "^(a+)+$", "level": "fail"}],
        }), encoding="utf-8")
        started = time.monotonic()
        with mock.patch("slop_lint.threading.current_thread", return_value=object()), \
                mock.patch.dict(os.environ, {"SLOP_LINT_TIMEOUT": "0.05"}):
            with self.assertRaisesRegex(SlopLintError, "초과"):
                lint_book(self.book)
        self.assertLess(time.monotonic() - started, 3)

    def test_spawn_worker_fallback_scans_without_posix_signals(self):
        self.write_book("# 테스트\n\n본문 — 검출\n", mode="warn")
        patterns, _ = load_patterns(self.book)

        findings = _scan_in_worker(resolve_chapter_files(self.book), patterns, 3)

        self.assertEqual(1, len([item for item in findings if item["level"] == "fail"]))

    def test_strict_cli_exits_two_for_fail_finding(self):
        self.write_book("# 테스트\n\n본문 — 검출\n", mode="strict")

        result = self.run_script("slop_lint.py")

        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn("FAIL", result.stdout)


class BuildHtmlTests(unittest.TestCase):
    def test_print_path_preserves_raw_html_while_preview_path_escapes_it(self):
        source = (
            "::: pull\n<em>인용</em>\n<strong>화자</strong>\n:::\n\n"
            "::: info <b>제목</b>\n<script>본문</script>\n:::\n"
        )

        printed = md_to_html(source)
        previewed = md_to_html(source, allow_html=False)

        self.assertIn("<em>인용</em>", printed)
        self.assertIn("<strong>화자</strong>", printed)
        self.assertIn('<div class="callout-title"><b>제목</b></div>', printed)
        self.assertIn("<script>본문</script>", printed)
        self.assertIn("&lt;em&gt;인용&lt;/em&gt;", previewed)
        self.assertIn("&lt;strong&gt;화자&lt;/strong&gt;", previewed)
        self.assertIn("&lt;b&gt;제목&lt;/b&gt;", previewed)
        self.assertIn("&lt;script&gt;본문&lt;/script&gt;", previewed)


class PreviewTests(BookFixture):
    def test_preview_escapes_raw_html_uses_nonce_csp_and_marks_exact_finding(self):
        self.write_book(
            "# 테스트\n\n"
            "<style>.lintpanel{display:none}</style>\n\n"
            "본문 — 검출\n\n"
            "`코드 — 제외`\n"
        )

        result = self.run_script("preview.py")

        self.assertEqual(0, result.returncode, result.stderr)
        html = (self.book / "preview" / "manuscript.html").read_text(encoding="utf-8")
        self.assertIn("&lt;style&gt;.lintpanel{display:none}&lt;/style&gt;", html)
        self.assertNotIn("style-src 'unsafe-inline'", html)
        self.assertRegex(html, r"style-src 'nonce-[A-Za-z0-9_-]+'")
        self.assertRegex(html, r'<style nonce="[A-Za-z0-9_-]+">')
        self.assertEqual(1, html.count('<mark class="slop">'))
        self.assertIn("<code>코드 — 제외</code>", html)

    def test_atomic_output_does_not_follow_prepositioned_symlinks(self):
        self.write_book()
        preview_dir = self.book / "preview"
        preview_dir.mkdir()
        temp_sentinel = self.root / "temp-sentinel.txt"
        out_sentinel = self.root / "out-sentinel.txt"
        temp_sentinel.write_text("TEMP SAFE", encoding="utf-8")
        out_sentinel.write_text("OUT SAFE", encoding="utf-8")
        try:
            (preview_dir / ".manuscript.html.tmp").symlink_to(temp_sentinel)
            (preview_dir / "manuscript.html").symlink_to(out_sentinel)
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")

        result = self.run_script("preview.py")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("TEMP SAFE", temp_sentinel.read_text(encoding="utf-8"))
        self.assertEqual("OUT SAFE", out_sentinel.read_text(encoding="utf-8"))
        output = preview_dir / "manuscript.html"
        self.assertFalse(output.is_symlink())
        self.assertIn("<!doctype html>", output.read_text(encoding="utf-8"))

    def test_link_destination_is_not_linted_or_modified_by_highlight_marker(self):
        self.write_book(
            "# 테스트\n\n"
            "[링크](https://example.test/a—b)\n\n"
            "본문 — 검출\n"
        )

        lint = lint_book(self.book)
        result = self.run_script("preview.py")

        self.assertEqual(1, lint["counts"]["fail"])
        self.assertEqual(0, result.returncode, result.stderr)
        html = (self.book / "preview" / "manuscript.html").read_text(encoding="utf-8")
        self.assertNotIn("%EE%80%80bf-", html)
        self.assertIn("https://example.test/a%E2%80%94b", html)
        self.assertEqual(1, html.count('<mark class="slop">'))

    def test_commonmark_link_boundaries_mask_only_valid_destinations(self):
        self.write_book(
            "# 테스트\n\n"
            "일반](문장 — 검출)\n\n"
            "[멀티라인 링크](\nhttps://example.test/a—b\n)\n"
        )

        lint = lint_book(self.book)
        result = self.run_script("preview.py")

        self.assertEqual(1, lint["counts"]["fail"])
        self.assertIn("일반](문장 — 검출)", lint["findings"][0]["excerpt"])
        self.assertEqual(0, result.returncode, result.stderr)
        html = (self.book / "preview" / "manuscript.html").read_text(encoding="utf-8")
        self.assertNotIn("%EE%80%80bf-", html)
        self.assertIn("https://example.test/a%E2%80%94b", html)
        self.assertEqual(1, html.count('<mark class="slop">'))

    def test_link_parser_does_not_cross_blank_paragraph_boundary(self):
        self.write_book(
            "# 테스트\n\n"
            "[끊긴 링크](\n\n"
            "https://example.test/a—b\n"
            ")\n"
        )

        lint = lint_book(self.book)

        self.assertEqual(1, lint["counts"]["fail"])
        self.assertEqual(5, lint["findings"][0]["line"])

    def test_overlapping_findings_highlight_their_union(self):
        self.write_book("# 테스트\n\nabcd\n")
        (self.book / "slop-patterns.json").write_text(json.dumps({
            "patterns": [
                {"id": "left", "regex": "abc", "level": "fail"},
                {"id": "right", "regex": "bcd", "level": "warn"},
            ],
        }), encoding="utf-8")

        result = self.run_script("preview.py")

        self.assertEqual(0, result.returncode, result.stderr)
        html = (self.book / "preview" / "manuscript.html").read_text(encoding="utf-8")
        self.assertIn('<mark class="slop">abcd</mark>', html)
        self.assertEqual(1, html.count('<mark class="slop">'))

    def test_preview_rejects_chapters_directory_symlink(self):
        self.write_book()
        chapter = self.book / "chapters" / "ch-01.md"
        chapter.unlink()
        chapters = self.book / "chapters"
        chapters.rmdir()
        external_dir = self.root / "external-chapters"
        external_dir.mkdir()
        (external_dir / "ch-01.md").write_text("# 테스트\n\n외부 내용", encoding="utf-8")
        try:
            chapters.symlink_to(external_dir, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")

        result = self.run_script("preview.py")

        self.assertNotEqual(0, result.returncode)
        self.assertIn("fail-closed", result.stderr + result.stdout)

    def test_preview_rejects_invalid_utf8_without_traceback(self):
        self.write_book(mode="off")
        (self.book / "chapters" / "ch-01.md").write_bytes(b"# title\n\xff\xfe")

        result = self.run_script("preview.py")

        self.assertNotEqual(0, result.returncode)
        self.assertIn("fail-closed", result.stderr + result.stdout)
        self.assertNotIn("Traceback", result.stderr)


class QcGateTests(BookFixture):
    def test_extension_style_path_is_allowed_without_fixed_whitelist(self):
        skill = self.root / "skill"
        style_dir = skill / "styles" / "custom-paper"
        style_dir.mkdir(parents=True)
        tokens = style_dir / "tokens.json"
        tokens.write_text("{}", encoding="utf-8")

        self.assertEqual(tokens, resolve_style_tokens(skill, "custom-paper"))
        for invalid in ([], {}, "../outside", "nested/style"):
            with self.subTest(invalid=invalid), self.assertRaises(SlopLintError):
                resolve_style_tokens(skill, invalid)

    def test_invalid_initial_config_writes_structured_failure_report(self):
        self.write_book()
        (self.book / "book.json").write_text("{not json", encoding="utf-8")

        result = self.run_script("qc_gate.py")

        self.assertEqual(1, result.returncode, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        report = json.loads((self.book / "gate-report.json").read_text(encoding="utf-8"))
        self.assertFalse(report["gates"]["CONFIG"]["ok"])
        self.assertIn("book.json", report["gates"]["CONFIG"]["error"])

    def test_invalid_style_type_writes_structured_failure_report(self):
        self.write_book()
        book = json.loads((self.book / "book.json").read_text(encoding="utf-8"))
        book["style"] = []
        (self.book / "book.json").write_text(json.dumps(book), encoding="utf-8")

        result = self.run_script("qc_gate.py")

        self.assertEqual(1, result.returncode, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        report = json.loads((self.book / "gate-report.json").read_text(encoding="utf-8"))
        self.assertFalse(report["gates"]["CONFIG"]["ok"])
        self.assertIn("style", report["gates"]["CONFIG"]["error"])

    def test_g13_strict_failure_precedes_pdf_gate_and_writes_report(self):
        self.write_book("# 테스트\n\n본문 — 검출\n", mode="strict")

        result = self.run_script("qc_gate.py")

        self.assertEqual(1, result.returncode, result.stderr)
        report = json.loads((self.book / "gate-report.json").read_text(encoding="utf-8"))
        self.assertFalse(report["gates"]["G13"]["ok"])
        self.assertNotIn("G1", report["gates"])

    def test_g13_config_error_writes_fail_closed_report(self):
        self.write_book(mode="strict")
        (self.book / "slop-patterns.json").write_text(json.dumps({"pattern": []}), encoding="utf-8")

        result = self.run_script("qc_gate.py")

        self.assertEqual(1, result.returncode, result.stderr)
        report = json.loads((self.book / "gate-report.json").read_text(encoding="utf-8"))
        self.assertIn("error", report["gates"]["G13"])
        self.assertFalse(report["gates"]["G13"]["ok"])

    def test_nonfinite_timeout_writes_g13_fail_closed_report(self):
        self.write_book(mode="strict")

        result = self.run_script("qc_gate.py", extra_env={"SLOP_LINT_TIMEOUT": "nan"})

        self.assertEqual(1, result.returncode, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        report = json.loads((self.book / "gate-report.json").read_text(encoding="utf-8"))
        self.assertFalse(report["gates"]["G13"]["ok"])
        self.assertIn("error", report["gates"]["G13"])

    def test_g13_warn_records_warning_and_continues_to_g1(self):
        self.write_book("# 테스트\n\n본문 — 검출\n", mode="warn")

        result = self.run_script("qc_gate.py")

        self.assertEqual(1, result.returncode, result.stderr)
        report = json.loads((self.book / "gate-report.json").read_text(encoding="utf-8"))
        self.assertTrue(report["gates"]["G13"]["ok"])
        self.assertEqual(1, report["gates"]["G13"]["counts"]["fail"])
        self.assertIn("G1", report["gates"])
        self.assertFalse(report["gates"]["G1"]["ok"])
        self.assertTrue(any("G13" in warning for warning in report["warns"]))

    def test_invalid_utf8_chapter_writes_g10_fail_closed_report(self):
        self.write_book()
        (self.book / "chapters" / "ch-01.md").write_bytes(b"# title\n\xff\xfe")

        result = self.run_script("qc_gate.py")

        self.assertEqual(1, result.returncode, result.stderr)
        report = json.loads((self.book / "gate-report.json").read_text(encoding="utf-8"))
        self.assertFalse(report["gates"]["G10"]["ok"])
        self.assertNotIn("G13", report["gates"])
        self.assertIn("fail-closed", result.stderr)


if __name__ == "__main__":
    unittest.main()
