# Dokumentasi teknis GraphMethod

GraphMethod adalah GitHub App tanpa frontend yang mereview pull request otomatis. Diff PR menjadi input utama, sedangkan potongan kode terkait dipilih melalui structural graph retrieval di Neo4j sebelum dikirim ke Gemini dalam satu prompt terstruktur.

Dokumentasi ini menjelaskan implementasi yang ada saat ini, bukan rancangan hipotetis.

## Daftar dokumen

1. [Arsitektur high-level](01-arsitektur-high-level.md) — komponen runtime, trust boundary, dan alur end-to-end.
2. [Desain low-level](02-desain-low-level.md) — modul, data model, state job, validasi, dan publishing.
3. [Indexing dan graph retrieval](03-indexing-dan-graph-retrieval.md) — AST Python, graph Neo4j, scoring, dan budget context.
4. [Prompt dan integrasi Gemini](04-prompt-dan-integrasi-gemini.md) — konstruksi prompt, request HTTP, schema, parsing, dan retry.
5. [Operasional dan deployment](05-operasional-dan-deployment.md) — topologi, konfigurasi, CI/CD, observability, dan batasan.

## Peta source code

| Area | Implementasi |
|---|---|
| API/lifecycle | `src/codereviewer/main.py` |
| Webhook/security | `webhooks.py`, `security.py` |
| Queue/data model | `queue.py`, `models.py`, `db.py` |
| Worker/orchestration | `worker.py`, `review.py` |
| GitHub/diff | `github.py`, `diff.py` |
| AST/graph/retrieval | `indexer.py`, `graph.py`, `retrieval.py` |
| LLM provider | `providers/base.py`, `providers/gemini.py`, `providers/registry.py` |
| Konfigurasi | `config.py` |

## Ringkasan alur

`GitHub webhook -> PostgreSQL queue -> worker -> GitHub diff/snapshot -> Python AST -> Neo4j traversal -> budgeted prompt -> Gemini JSON -> validation -> PostgreSQL audit -> GitHub comments`.
