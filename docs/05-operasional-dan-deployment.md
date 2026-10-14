# Operasional dan deployment

## Topologi lokal

`deploy/local/compose.yaml` menjalankan empat service:

| Service | Process | Akses |
|---|---|---|
| `api` | Uvicorn/FastAPI | host port 8000 |
| `worker` | `python -m codereviewer.worker` | internal |
| `postgres` | PostgreSQL 16 Alpine | internal |
| `neo4j` | Neo4j 5 Community | host ports 7474/7687 |

PostgreSQL dan Neo4j memakai named volume. `docker compose down` mempertahankan data; `down -v` menghapusnya.

## Production Hostinger

Compose production memakai image GHCR yang sama untuk API/worker, Docker secrets, resource limits, restart policy, named volumes, dan dua network:

- `graphmethod-internal` untuk aplikasi, PostgreSQL, dan Neo4j;
- external `platform-internal` agar Kong dapat meneruskan traffic ke API.

Kong hanya mengekspos webhook dan health endpoints melalui HTTPS. Worker, PostgreSQL, dan Neo4j tidak perlu terbuka ke internet.

## CI/CD

`Jenkinsfile` menjalankan:

1. metadata commit dan GitHub status `PENDING`;
2. Docker test stage;
3. login GHCR;
4. build/push image dengan SHA tag dan `latest`;
5. runtime env serta secret files dengan permission ketat;
6. Compose pull/up dan health wait;
7. verifikasi API healthy dan worker running;
8. GitHub status akhir dan cleanup.

## Konfigurasi penting

| Variable | Default | Dampak |
|---|---:|---|
| `LLM_MODEL` | `gemini-3.8-flash` | Model Gemini. |
| `LLM_FALLBACK_MODEL` | `gemini-3.7-flash` | Model cadangan setelah model utama terus mengembalikan HTTP 503. Kosongkan untuk menonaktifkan fallback. |
| `LLM_MAX_RETRIES` | 4 | Retry internal untuk HTTP 408, 429, dan 5xx dari Gemini. |
| `LLM_RETRY_BASE_SECONDS` | 1 | Jeda dasar exponential backoff dengan jitter. |
| `GRAPH_MAX_HOPS` | 2 | Radius traversal, dibatasi 1–2. |
| `GRAPH_MAX_NODES_PER_SYMBOL` | 12 | Limit related context per seed. |
| `CONTEXT_TOKEN_BUDGET` | 10000 | Target heuristik diff/context. |
| `MAX_REPOSITORY_FILES` | 500 | Batas file Python snapshot. |
| `MAX_SOURCE_FILE_BYTES` | 250000 | Batas ukuran source. |
| `MAX_CHANGED_FILES` | 30 | Batas file diff. |
| `MAX_CHANGED_LINES` | 2000 | Batas baris tambah/hapus. |
| `MAX_FINDINGS` | 10 | Batas pasca-model. |
| `WORKER_POLL_SECONDS` | 2 | Poll ketika queue kosong. |
| `WORKER_MAX_ATTEMPTS` | 3 | Batas total attempt. |
| `DRY_RUN` | false | Melewati publishing GitHub saja. |

Secret mendukung nilai langsung dari environment atau file melalui suffix `_FILE`; production memakai mode file.

## Health dan observability

- `/health/live` hanya memastikan process API hidup.
- `/health/ready` memeriksa Neo4j dan PostgreSQL.
- Log worker mencatat lifecycle job/exception tanpa sengaja mencetak raw prompt.
- `review_runs` menyimpan provider/model, token usage, latency retrieval/model, status, dan error.
- `retrieved_contexts` menyimpan audit ranking context.

Belum ada metrics exporter, distributed tracing, dead-letter queue terpisah, atau admin UI.

## Skalabilitas

`SKIP LOCKED` memungkinkan banyak worker pada PostgreSQL yang sama. Namun dua job dapat membangun snapshot yang sama secara bersamaan karena belum ada distributed lock khusus. Pengunduhan blob source berjalan satu per satu, dan tree yang truncated ditolak alih-alih dipaginasi.

Traversal dibatasi dua hop. Prompt memakai budget heuristik, bukan tokenizer provider. Keduanya menahan biaya prototype tetapi perlu diukur ulang untuk repository besar.

## Security checklist

- Gunakan HTTPS dan webhook secret yang cocok.
- Batasi permission GitHub App dan installation scope.
- Simpan GitHub private key/Gemini key di secret manager.
- Jangan log raw prompt karena mengandung source.
- Jangan import atau mengeksekusi source PR.
- Tinjau kebijakan data Gemini sebelum private repository.
- Rotasi secret yang pernah masuk log, chat publik, atau Git history.

## Batasan implementasi

- Full structural indexing hanya untuk Python.
- Resolusi relasi berbasis nama dan konservatif.
- Schema DB dibuat dengan `metadata.create_all`; belum ada Alembic.
- Hanya provider `gemini` yang terdaftar.
- `RETRIEVAL_MODE` wajib `graph`.
- Belum ada command manual `/review` atau Check Runs.
- Semua provider error memakai retry policy generik.
- `supporting_context_ids` model belum menjadi audit finding di PostgreSQL.
