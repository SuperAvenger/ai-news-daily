# Roadmap

## Now

- Separate source collection, ranking, summarization, and delivery behind small interfaces.
- Add fixture-based tests for malformed feeds and provider responses.
- Emit run summaries with source success rates, deduplication counts, and delivery status.

## Next

- Share the generic feed pipeline with the RSS digest project as a package.
- Add per-user topics, source allowlists, and deterministic ranking before LLM summarization.
- Expose read-only MCP tools for searching the latest collected items and source status.

## Later

- Add a reviewable archive with citations and retention controls.
- Keep outbound publishing as a separate, clearly annotated tool requiring confirmation.
