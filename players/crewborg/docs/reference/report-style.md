# Report style — crewborg's HTML reports (and how to adapt them)

crewborg's analysis skills (`crewrift-survey`, `crewrift-diagnose`, `crewrift-experiment`) each render
a self-contained HTML report. Each ships a **generator script** that produces a solid default in the
Softmax **"Ink & Print"** house style. **The generator is a starting point, not a straitjacket** — when
the content needs a layout the template doesn't have (a comparison better as a table, a sequence
better as a flow, a finding that needs its own emphasis), **author or adapt the HTML directly** so it
fits *what you're saying*. Don't force content into the wrong shape, and don't ship a page you haven't
looked at.

## The non-negotiables (what "good" means here)

- **Typography drives hierarchy, not boxes.** Serif (`Merriweather`) headings, sans
  (`Merriweather Sans`) body, **mono (`IBM Plex Mono`, `tnum`) for all numbers** (scores, rates, ids).
  Section headings are small, **uppercase, letter-spaced, navy**.
- **No decorative cards.** A border must earn its keep (it groups, bounds an independent unit, or is
  one clickable object) — otherwise let content sit in the scene separated by hairline rules +
  whitespace. No gradient washes, drop shadows, or even card grids.
- **Finding-first.** Lead with the conclusion/headline; supporting detail follows. Reports are read
  top-to-bottom like a broadsheet, one column.
- **Semantic colour, used sparingly.** `--sage #6e8050` = good/win/confirmed · `--terra #b36e4e` =
  bad/loss/refuted/error · `--gold #d4a853` = pending/medium · `--navy #1a3875` = headings/structure ·
  `--exclusive #8f5b3f` = editorial emphasis. Never pure `#000`/`#fff` text.
- **Real empty/edge states** — an empty table keeps its headers + an italic "none" line; never a blank.
- **Responsive** — wide tables scroll in an `overflow-x:auto` wrapper; it must hold at 375 / 768 / 1280.

The full palette + type spec is the Ink & Print system (the `ux.ify` skill's `design-system.md`);
the three generators already encode it — **copy their `STYLE` block** as the base when you hand-author.

## The shared building blocks (reuse these, compose freely)

| Block | Use |
|---|---|
| masthead (eyebrow + serif H1 + meta) | the title + one-line context |
| `.signals` / `.detail` callout (navy left-border) | an explanation or a code/query block (`<pre>` mono inside) |
| labeled `.row` grid (`k`/`v`) | a record's fields (evidence / mechanism / …) |
| `.preds` two-column cards (sage / terracotta) | a true-vs-false or A-vs-B comparison |
| `.flow` step strip with `→` | a sequence (hypothesis → instrument → verdict) |
| chips (`.c-high/medium/low`, verdict) | confidence / status, never as decoration |
| data table (`.data`: mono right-aligned nums, header rule, row hover) | any aggregate |
| heat-map cells (sage→terracotta blend) | a matrix |

When the default doesn't fit: **add a block, restructure the order, or build a new visual** (a small
table, a flow, a chart) — keeping the tokens + the non-negotiables above. Adapting the layout to the
argument is the point; conforming the *style* is the constraint.

## Verify by looking — mandatory before you present

Code can't see clipping, overflow, or "this reads wrong." After generating or editing a report,
**look at it**:

```bash
cd <dir-with-the-html>; python3 -m http.server 8799 &        # Playwright can't load file://
node "$UXIFY_DIR/scripts/shoot.mjs" http://localhost:8799/<file>.html <tag> --out=/tmp/shots
# then Read the PNGs at 375 / 768 / 1280, fix, re-shoot until it's right.
```

Or run the **`ux.ify`** skill on the file — it applies this same bar and screenshots every breakpoint.
Only present a report you've actually seen render.
