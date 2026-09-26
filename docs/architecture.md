# Architecture

## Pipeline contracts

1. `extract_template.py` parses the source OOXML package without rasterizing it. The output retains master/layout/slide indices, placeholder geometry, exact theme colors, text runs, backgrounds, tables, and media metadata.
2. `renderer.py` produces geometry-accurate PNG schematics without requiring PowerPoint or LibreOffice. A multimodal model can use them to assess visual hierarchy, rhythm, density, and imagery; exact tokens remain grounded in OOXML.
3. `analyzer.py` classifies the source model and normalizes low-level context into a stable design system and semantic pattern catalog. PDF-derived decks are identified by their fragment ratio and receive explicit `native-cover`, `native-cards`, `native-list`, `native-split`, and `native-closing` patterns. Analysis directories use a SHA-256 suffix, so the same template is reused safely.
4. `planner.py` sends compact style constraints, available pattern capacities, requested slide count, and source content to an OpenAI-compatible chat-completions endpoint. The response is normalized and constrained before composition. A deterministic planner is available for offline execution.
5. `assign_patterns` scores each slide against layouts, real exemplars, and generated native-grid patterns. The score combines semantic role, rhetorical pattern, capacity, bullet slots, data/image support, complexity, prior QA failures, and a diversity penalty. The plan records `why_fit`, `risk`, and three alternatives for every selection.
6. `composer.py` uses a dual path. Semantic templates keep their native layouts/exemplars. Fragmented PDF conversions never clone source text or vector fragments: the composer starts from a blank layout, copies only recurring brand imagery, and rebuilds the composition with editable text boxes, panels, shapes, charts, and tables using extracted grid and design tokens.
6a. `contextual_audit.py` optionally reviews the shared plan with the configured text model before the three layout variants are built. It emits quote-grounded editorial suggestions, which are shown in QA beside deterministic geometric issues. It does not inspect slide images or certify facts.
6b. After exporting all variants, `visual_diversity.py` compares rendered content slides. If two variants are too similar, the service can rebuild selected slides of one variant with distinct patterns already present in the same template, then checks QA and pixel differences again.
7. `qa.py` reopens the result and verifies ZIP/OOXML validity, slide count, shape bounds, estimated text overflow, text-box overlap, empty slides, font fidelity, absence of leaked PDF fragments, and use of native objects for fragmented sources. A failed attempt compacts at word boundaries, excludes failed patterns, remaps, and regenerates.
8. `powerpoint.py` exports every generated slide through PowerPoint COM on Windows and checks slide completeness and visually blank renders. Its result is merged into the same QA report before the retry decision. `BRANDDECK_POWERPOINT_QA=0` disables this stage in CI; the default is `auto`.
9. `jobs.py` persists API job metadata and receives progress callbacks from `service.py`. FastAPI exposes queue, polling, and download endpoints while retaining the synchronous endpoint for small requests.

## Job lifecycle

```text
POST /v1/presentations/jobs
  -> queued
  -> template_analysis
  -> content_planning
  -> layout_mapping
  -> composition
  -> quality_assurance
  -> completed | failed
```

The local background runner is intentionally dependency-free and suitable for a
single API process. Completed state survives restart, but interrupted work does
not resume automatically; distributed deployment should replace the runner with
an external queue while keeping the same job contract.

## Trust boundaries

- The LLM controls narrative structure and structured visual intent, not raw OOXML or executable code.
- Model output is parsed as JSON, allow-listed by slide/visual type, length-limited, and normalized.
- Source claims and numbers must not be invented. The planner prompt explicitly limits evidence to the supplied content.
- The original template is never modified. Every generation writes a new package.
- Uploaded API templates are copied to an isolated temporary directory and removed after analysis.

## Extending visual types

Add the JSON contract to `PLANNER_SYSTEM`, allow it in `_sanitize_visual`, and implement a composer function receiving `(slide, visual, zone, design)`. Name generated shapes with a `BrandDeck` prefix so QA and future editing tools can identify them.

## Computer-vision stage

Representative slides are rasterized into `previews/` after extraction. When `INFERENCE_VISION=1`, up to four previews are sent alongside the structured context to a multimodal Inference API. If the selected model rejects images, the analyzer automatically retries with JSON only. Set `INFERENCE_VISION=0` when templates must not be sent as images.

## Legacy compatibility

The original `.github/agents`, prompts, document converter, extractor, and PPTXGenJS runner remain available. The new Python path is the default because it preserves the source PowerPoint package directly and does not require Node.js.
