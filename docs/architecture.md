# Architecture

## Pipeline contracts

1. `extract_template.py` parses the source OOXML package without rasterizing it. The output retains master/layout/slide indices, placeholder geometry, exact theme colors, text runs, backgrounds, tables, and media metadata.
2. `renderer.py` produces geometry-accurate PNG schematics without requiring PowerPoint or LibreOffice. A multimodal model can use them to assess visual hierarchy, rhythm, density, and imagery; exact tokens remain grounded in OOXML.
3. `analyzer.py` normalizes the low-level context into a stable design system and a semantic pattern catalog. Analysis directories use a SHA-256 suffix, so the same template is reused safely.
4. `planner.py` sends compact style constraints, available pattern capacities, requested slide count, and source content to an OpenAI-compatible chat-completions endpoint. The response is normalized and constrained before composition. A deterministic planner is available for offline execution.
5. `assign_patterns` scores each slide against layouts and real slide exemplars from the current template. The score combines semantic role, rhetorical pattern, title/body capacity, bullet slots, data/image support, complexity, prior QA failures, and a tiny reuse penalty. The plan records `why_fit`, `risk`, and three alternatives for every selection.
6. `composer.py` opens the original template package. When a selected layout has representative slides, it clones the sample shape tree and remaps its media/chart relationships. Otherwise it adds a native slide from the matching master/layout. Text placeholders keep inherited typography. Generated data visuals use extracted theme tokens. Full-bleed backgrounds and recurring brand assets survive, while unmatched source screenshots, logos, icons, and portraits are removed.
7. `qa.py` reopens the result and verifies ZIP/OOXML validity, slide count, shape bounds, estimated text overflow, text-box overlap, empty slides, and explicit font fidelity. A failed or low-scoring attempt compacts content, excludes failed exemplars for affected slides, remaps layouts, and regenerates the deck.
8. `jobs.py` persists API job metadata and receives progress callbacks from `service.py`. FastAPI exposes queue, polling, and download endpoints while retaining the synchronous endpoint for small requests.

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
