# bookforge

**One-line topic → commercial-book-quality ebook PDF.** An agent skill that runs on both Claude Code and OpenAI Codex.

[한국어](README.md)

bookforge produces PDFs with real book anatomy — cover, leader-dot TOC, chapter openers, running heads, colophon. Content is written in plain Markdown; typesetting is owned by six style packs and deterministic scripts; quality is physically enforced by QC gates — a PDF that fails the gates cannot exist in `final/`.

## Six examples — every one produced by this skill

Six topics × six styles (all in Korean, showcasing Korean book typography). Click a cover to open the full PDF.

| | | |
|:---:|:---:|:---:|
| [![practical](examples/showcase/practical-prompt-patterns-cover.png)](examples/practical-prompt-patterns.pdf) | [![insight](examples/showcase/insight-ondevice-ai-cover.png)](examples/insight-ondevice-ai.pdf) | [![academic](examples/showcase/academic-game-theory-cover.png)](examples/academic-game-theory.pdf) |
| **practical** how-to book<br>*24 Prompt Patterns*, 28p | **insight** tech report<br>*On-Device AI 2026*, 30p | **academic** scholarly<br>*Foundations of Game Theory*, 35p |
| [![essay](examples/showcase/essay-evening-sentences-cover.png)](examples/essay-evening-sentences.pdf) | [![business](examples/showcase/business-sme-ai-cover.png)](examples/business-sme-ai.pdf) | [![magazine](examples/showcase/magazine-trend-brief-cover.png)](examples/magazine-trend-brief.pdf) |
| **essay** minimal prose<br>*Sentences on the Way Home*, 32p | **business** consulting paper<br>*SME AI Adoption Strategy*, 28p | **magazine** trend issue<br>*TREND BRIEF*, 25p |

## The six style packs

Each style ships a design rulebook (`styles/*/STYLE.md`) distilled from measured commercial publications — trim size in mm, font sizes in pt, leading, color tokens, page templates, and hard prohibitions.

| Style | Identity | Trim | Engine |
|---|---|---|---|
| `practical` | IT how-to books, step-by-step guides | 153×225 | Typst |
| `insight` | Tech-trend research reports | 182×257 | HTML→Chromium |
| `academic` | Scholarly monographs (booktabs tables, numbered sections) | 153×225 | Typst |
| `essay` | Minimal literary prose (1-ink + 1 accent) | 128×188 | Typst |
| `business` | Consulting white papers (navy system, action titles, key stats) | 200×280 | Typst |
| `magazine` | Trend magazines (editorial grid, full-page pull quotes) | 200×265 | HTML→Chromium |

## Install

```bash
git clone https://github.com/gongnyang/bookforge.git
cd bookforge
ln -sfn "$PWD" ~/.claude/skills/bookforge   # Claude Code
ln -sfn "$PWD" ~/.codex/skills/bookforge    # Codex CLI
ln -sfn "$PWD" ~/.agents/skills/bookforge   # shared agents dir
```

Requirements (self-checked by the skill): **Typst 0.14+**, **Python 3 + PyMuPDF + markdown-it-py**, and **Playwright Chromium** for the two HTML-engine styles only. Five OFL fonts are bundled ([notice](assets/fonts/LICENSES.md)) — everything renders out of the box.

## Use

Just ask your agent:

```
"Make an ebook about on-device AI trends in the insight style"   ← topic mode
"Typeset this manuscript (draft.docx) as an essay collection"    ← manuscript mode
```

Or drive it manually:

```bash
python3 scripts/scaffold.py mybook --style essay --title "Title" --length short
# write chapters/*.md + outline.json, then
python3 scripts/preview.py mybook      # → preview/manuscript.html (manuscript + slop review)
python3 scripts/build.py mybook        # → draft/book.pdf
python3 scripts/qc_gate.py mybook      # gates pass → final/mybook.pdf
```

## Quality gates

Page breaking, filling, and intentional whitespace follow a pagination rulebook ([references/pagination.md](references/pagination.md)) distilled from measurements of commercial Korean books; density gates catch both unjustified emptiness and forced filler.

Only the gate script can create `final/`. Before rendering, G10 verifies that quotations and figures exist in the manuscript and G13 checks AI-tell fingerprints (`strict`/`warn`/`off`). The remaining gates cover rendering, page range, embedded fonts, overflow, TOC/page alignment, density, intentional whitespace, keep/widow rules, role declarations, and parity; G6 is the agent's visual inspection of rendered pages.

Generated art policy: cover/body art must be **text-free generated images**; all lettering is set as vectors by the layout layer, and books containing generated images say so in captions and the colophon.

## License

Code & docs: MIT. Bundled fonts: OFL 1.1 ([notice](assets/fonts/LICENSES.md)). The six example PDFs are demo outputs of the skill.
