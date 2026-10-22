# GraphMethod AI Pull Request Reviewer

Implementasi Master SRS 3.0.0, pemetaan FR-01–FR-08, konfigurasi repo, prosedur
upgrade, dan batas verifikasi tersedia di [laporan SRS](docs/06-srs-implementation.md).
Contoh konfigurasi repo: [`.graphmethod.example.yml`](.graphmethod.example.yml).

> Untuk deployment ke VM Hostinger yang memakai Jenkins, Kong, dan HTTPS, ikuti
> [DEPLOYMENT_HOSTINGER.md](DEPLOYMENT_HOSTINGER.md).
> Untuk memasang Certificate, SNI, Service, dan Route dari halaman Kong Manager,
> ikuti [KONG_MANAGER_TLS_ROUTING.md](KONG_MANAGER_TLS_ROUTING.md).

GraphMethod adalah GitHub App tanpa frontend yang otomatis melakukan code review pada pull request. Reviewer menggunakan PR diff sebagai input utama dan mengambil konteks tambahan melalui structural graph retrieval di Neo4j.

Hasil review muncul langsung di GitHub dalam bentuk:

- Satu summary comment yang diperbarui pada setiap commit baru.
- Inline review comments pada baris yang berubah.
- Suggested change yang dapat diterapkan developer.
- Review dengan status `COMMENT`, bukan automatic approval atau rejection.
- Quality gate berbobot dengan Check Run dan commit status: pending selama analisis,
  success jika skor memenuhi threshold, failure jika tidak.

Dokumentasi arsitektur lengkap tersedia di [`docs/README.md`](docs/README.md).

## 1. Arsitektur runtime

```text
GitHub Pull Request
        │
        │ webhook
        ▼
FastAPI Webhook API
        │
        │ enqueue
        ▼
PostgreSQL Job Queue
        │
        │ claim
        ▼
Python Review Worker
        ├── GitHub API
        ├── Tree-sitter Python / JS / TS Indexer
        ├── Neo4j Graph Retrieval
        ├── Gemini/LLM Provider
        └── GitHub Review Publisher
```

GitHub menjadi user interface aplikasi. Tidak ada web dashboard terpisah.

## 2. Prasyarat

Untuk menjalankan seluruh stack dengan cara yang direkomendasikan:

- Docker Desktop dengan Docker Compose.
- Akun GitHub yang dapat membuat GitHub App.
- Gemini API key dari Google AI Studio.
- HTTPS tunnel atau public HTTPS endpoint untuk menerima webhook GitHub.

Python lokal hanya diperlukan jika ingin menjalankan test atau development tanpa container aplikasi.

## 3. Membuat Gemini API key

1. Buka [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Login menggunakan akun Google.
3. Buat atau pilih Google Cloud project.
4. Pilih **Create API key**.
5. Simpan API key tersebut dengan aman.

API key hanya dimasukkan ke `.env` pada server. Jangan memasukkannya ke source code, commit Git, GitHub comment, atau repository secret yang dapat dibaca workflow tidak tepercaya.

Konfigurasi default menggunakan:

```env
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.8-flash
LLM_FALLBACK_MODEL=gemini-3.7-flash
```

Nama model dapat diganti melalui environment variable tanpa mengubah review pipeline.

> Periksa kebijakan data provider sebelum menggunakan free tier untuk private atau proprietary repository. Embedding tidak digunakan pada metode ini, tetapi source context tetap dikirim ke model review.

## 4. Membuat GitHub App

### 4.1 Registrasi awal

1. Buka GitHub.
2. Masuk ke **Settings**.
3. Pilih **Developer settings**.
4. Pilih **GitHub Apps**.
5. Pilih **New GitHub App**.

Isi konfigurasi dasar:

```text
GitHub App name: nama unik, misalnya zaim-graphmethod-reviewer
Homepage URL: URL repository proyek ini
Webhook: Active
Webhook URL: isi setelah HTTPS tunnel tersedia
Webhook secret: random secret yang panjang
```

User authorization atau OAuth callback tidak diperlukan untuk MVP karena aplikasi bekerja sebagai installation, bukan atas nama user.

### 4.2 Repository permissions

Gunakan permission minimum:

| Permission | Level | Kegunaan |
|---|---|---|
| Metadata | Read | Informasi repository |
| Contents | Read | Membaca source dan commit |
| Pull requests | Read and write | Membaca diff dan menulis review |
| Issues | Read | Menerima event Issue comment untuk `/review` |
| Commit statuses | Read and write | Menampilkan status analisis dan memblokir merge |
| Checks | Read and write | Quality gate dengan skor dan threshold per repository |

Jika App sudah pernah di-install, perubahan permission harus disetujui kembali pada
installation repository/organization sebelum status dapat dibuat.

Aplikasi tidak membutuhkan `Contents: Write`, sehingga tidak dapat melakukan commit atau mengubah source code repository.

### 4.3 Webhook subscription

Subscribe ke event:

```text
Pull request
Issue comment
```

Action yang diproses oleh aplikasi:

```text
opened
synchronize
reopened
ready_for_review
```

Event atau action lain akan diterima tanpa menghasilkan review job.

### 4.4 Installation scope

Untuk prototype, pilih:

```text
Only on this account
```

Setelah GitHub App dibuat, catat nilai **App ID**. Nilai ini berbeda dari Client ID.

### 4.5 Private key

Pada halaman GitHub App:

1. Buka bagian **Private keys**.
2. Pilih **Generate a private key**.
3. GitHub akan mengunduh file `.pem`.
4. Jangan commit file tersebut.

Project menerima private key sebagai base64 agar aman dimasukkan sebagai satu baris environment variable.

PowerShell:

```powershell
$keyBytes = [System.IO.File]::ReadAllBytes("C:\path\to\github-app.private-key.pem")
[Convert]::ToBase64String($keyBytes) | Set-Clipboard
```

Bash:

```bash
base64 < /path/to/github-app.private-key.pem | tr -d '\n'
```

Tempel hasilnya ke `GITHUB_PRIVATE_KEY_BASE64` di `.env`. Jangan tempel private key atau hasil base64 ke issue, PR, chat publik, atau terminal log.

## 5. Menyiapkan environment variables

Dari folder `graphmethod`, salin template:

PowerShell:

```powershell
Copy-Item .env.example .env
```

Bash:

```bash
cp .env.example .env
```

Kemudian edit `.env`:

```env
APP_ENV=development
LOG_LEVEL=INFO

# Alamat service di dalam Docker Compose network.
DATABASE_URL=postgresql+asyncpg://codereviewer:codereviewer@postgres:5432/codereviewer
NEO4J_URI=bolt://neo4j:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=codereviewer-password

# GitHub App.
GITHUB_APP_ID=123456
GITHUB_PRIVATE_KEY_BASE64=<BASE64_DARI_FILE_PEM>
GITHUB_WEBHOOK_SECRET=<RANDOM_WEBHOOK_SECRET>
GITHUB_API_URL=https://api.github.com

# LLM provider.
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.8-flash
LLM_FALLBACK_MODEL=gemini-3.7-flash
LLM_API_KEY=<GEMINI_API_KEY>
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta
LLM_MAX_RETRIES=4
LLM_RETRY_BASE_SECONDS=1

# Retrieval dan review limits.
RETRIEVAL_MODE=graph
GRAPH_MAX_HOPS=2
GRAPH_MAX_NODES_PER_SYMBOL=12
CONTEXT_TOKEN_BUDGET=10000
MAX_REPOSITORY_FILES=500
MAX_SOURCE_FILE_BYTES=250000
MAX_CHANGED_FILES=30
MAX_CHANGED_LINES=2000
MAX_FINDINGS=10

# Worker behavior.
WORKER_POLL_SECONDS=2
WORKER_MAX_ATTEMPTS=3
DRY_RUN=false
```

### 5.1 Environment variable wajib

| Variable | Wajib | Keterangan |
|---|---|---|
| `DATABASE_URL` | Ya | PostgreSQL async SQLAlchemy URL |
| `NEO4J_URI` | Ya | Neo4j Bolt URI |
| `NEO4J_USERNAME` | Ya | Neo4j username |
| `NEO4J_PASSWORD` | Ya | Neo4j password |
| `GITHUB_APP_ID` | Ya | App ID dari GitHub App settings |
| `GITHUB_PRIVATE_KEY_BASE64` | Ya | Isi file private key yang di-encode base64 |
| `GITHUB_WEBHOOK_SECRET` | Ya | Secret yang sama dengan konfigurasi webhook |
| `LLM_PROVIDER` | Ya | Saat ini harus `gemini` |
| `LLM_MODEL` | Ya | Nama model Gemini |
| `LLM_FALLBACK_MODEL` | Tidak | Model cadangan untuk HTTP 503; kosongkan untuk menonaktifkan |
| `LLM_API_KEY` | Ya | Gemini API key |

### 5.2 Environment variable tuning

| Variable | Default | Fungsi |
|---|---:|---|
| `GRAPH_MAX_HOPS` | `2` | Maksimum kedalaman traversal graph |
| `GRAPH_MAX_NODES_PER_SYMBOL` | `12` | Maksimum related context per changed symbol |
| `CONTEXT_TOKEN_BUDGET` | `10000` | Budget perkiraan diff dan graph context |
| `MAX_REPOSITORY_FILES` | `500` | Batas file Python/JS/TS yang di-index |
| `MAX_SOURCE_FILE_BYTES` | `250000` | Batas ukuran satu file source |
| `MAX_CHANGED_FILES` | `30` | Batas jumlah file PR |
| `MAX_CHANGED_LINES` | `2000` | Batas changed lines PR |
| `MAX_FINDINGS` | `10` | Batas finding yang diproses |
| `WORKER_MAX_ATTEMPTS` | `3` | Maksimum percobaan job |
| `DRY_RUN` | `false` | Jika true, jangan publish ke GitHub |

Nilai tersebut merupakan batas prototype dan perlu dituning berdasarkan repository serta kuota model.

## 6. Menjalankan dengan Docker Compose

Konfigurasi Compose lokal berada di `deploy/local/compose.yaml`. Konfigurasi
produksi Hostinger berada terpisah di `deploy/hostinger/compose.yaml`.

Pastikan Docker Desktop sedang aktif, kemudian jalankan dari folder `graphmethod`:

```powershell
docker compose -f deploy/local/compose.yaml up --build
```

Service yang dijalankan:

| Service | Fungsi | Port host |
|---|---|---:|
| `api` | FastAPI webhook receiver | `8000` |
| `worker` | Indexing dan AI review | Tidak diekspos |
| `postgres` | Job dan review state | Internal Compose |
| `neo4j` | Code graph | `7474`, `7687` |

Jalankan di background:

```powershell
docker compose -f deploy/local/compose.yaml up --build -d
```

Lihat log:

```powershell
docker compose -f deploy/local/compose.yaml logs -f api worker
```

Hentikan stack tanpa menghapus data:

```powershell
docker compose -f deploy/local/compose.yaml down
```

Hindari `docker compose -f deploy/local/compose.yaml down -v` jika ingin mempertahankan graph dan database, karena flag `-v` menghapus volume data.

## 7. Memeriksa health service

Setelah stack aktif:

PowerShell:

```powershell
Invoke-RestMethod http://localhost:8000/health/live
Invoke-RestMethod http://localhost:8000/health/ready
```

Bash:

```bash
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
```

Respons yang diharapkan:

```json
{"status":"ok"}
```

dan:

```json
{"status":"ready"}
```

Perbedaannya:

- `/health/live` memastikan process API hidup.
- `/health/ready` memastikan API dapat terhubung ke PostgreSQL dan Neo4j.

Neo4j Browser tersedia di [http://localhost:7474](http://localhost:7474). Login menggunakan nilai `NEO4J_USERNAME` dan `NEO4J_PASSWORD`.

## 8. Mengekspos webhook melalui HTTPS

GitHub tidak dapat mengakses `localhost`. Untuk development, gunakan HTTPS tunnel pilihanmu sehingga:

```text
https://your-tunnel.example
        ↓
http://localhost:8000
```

Webhook URL GitHub App harus diarahkan ke:

```text
https://your-tunnel.example/webhooks/github
```

Setelah URL tunnel diperoleh:

1. Buka GitHub App settings.
2. Ubah **Webhook URL**.
3. Pastikan **Active** dipilih.
4. Pastikan webhook secret sama dengan `GITHUB_WEBHOOK_SECRET`.
5. Simpan perubahan.

Jangan menonaktifkan SSL verification untuk deployment production.

## 9. Meng-install GitHub App

1. Buka halaman settings GitHub App.
2. Pilih **Install App**.
3. Pilih akun atau organization.
4. Untuk prototype, pilih **Only select repositories**.
5. Pilih repository test yang tidak mengandung kode sensitif.
6. Selesaikan installation.

Installation ID tidak perlu dimasukkan ke `.env`. ID tersebut dikirim GitHub di dalam webhook payload.

## 10. Menjalankan review pertama

1. Buat branch baru pada repository tempat App di-install.
2. Ubah file Python.
3. Push branch.
4. Buat pull request.

GitHub akan mengirim event `pull_request.opened`.

Alur yang diharapkan:

```text
Webhook diterima
    ↓
Status "GraphMethod AI Review" menjadi pending
    ↓
Review job dibuat
    ↓
Worker mengambil PR diff
    ↓
Repository snapshot di-index ke Neo4j
    ↓
Changed symbols dan dependency diambil
    ↓
Gemini melakukan review
    ↓
Summary dan inline comments muncul di PR
    ↓
Status "GraphMethod AI Review" menjadi success
```

Ketika commit baru di-push ke PR, event `pull_request.synchronize` menjalankan review untuk head SHA baru.

## 11. Memverifikasi webhook

Pada GitHub App settings:

1. Buka **Advanced**.
2. Lihat **Recent Deliveries**.
3. Pilih delivery `pull_request`.
4. Pastikan response dari aplikasi adalah `202`.

Contoh response:

```json
{
  "accepted": true,
  "duplicate": false,
  "job_id": "..."
}
```

Webhook duplikat akan menghasilkan:

```json
{
  "accepted": false,
  "duplicate": true,
  "job_id": null
}
```

## 12. Dry-run mode

Untuk menguji indexing dan Gemini tanpa menulis comment ke GitHub:

```env
DRY_RUN=true
```

Restart API dan worker setelah mengubah `.env`:

```powershell
docker compose -f deploy/local/compose.yaml up -d --force-recreate api worker
```

Dalam dry-run mode:

- Webhook tetap diterima.
- Repository tetap di-index.
- Graph retrieval dan model review tetap berjalan.
- Review run serta finding tetap masuk PostgreSQL.
- Summary dan inline comments tidak dikirim ke GitHub.
- Commit status tidak dibuat atau diperbarui, sehingga mode ini tidak memblokir merge.

## 13. Menjalankan test lokal

Buat virtual environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install project dan development dependencies:

```powershell
python -m pip install -e ".[dev]"
```

Jalankan test:

```powershell
python -m pytest -q
```

Jalankan lint:

```powershell
python -m ruff check .
```

Jalankan formatter:

```powershell
python -m ruff format .
```

Test suite menggunakan mock untuk GitHub dan Gemini serta SQLite untuk queue test. Test tidak memerlukan API key atau service eksternal.

## 14. Menjalankan API dan worker di luar container

Mode ini berguna untuk development. PostgreSQL dan Neo4j tetap dapat dijalankan melalui Docker:

```powershell
docker compose -f deploy/local/compose.yaml up -d postgres neo4j
```

Karena process Python berjalan pada host, gunakan hostname `localhost`, bukan nama service Compose:

```env
DATABASE_URL=postgresql+asyncpg://codereviewer:codereviewer@localhost:5432/codereviewer
NEO4J_URI=bolt://localhost:7687
```

Jalankan API pada terminal pertama:

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn codereviewer.main:app --reload --host 0.0.0.0 --port 8000
```

Jalankan worker pada terminal kedua:

```powershell
.\.venv\Scripts\Activate.ps1
python -m codereviewer.worker
```

Kembalikan hostname `.env` ke `postgres` dan `neo4j` sebelum menjalankan API/worker di dalam Docker Compose.

## 15. Output pada pull request

### 15.1 Required status saat analisis

Setiap PR/commit yang diterima membuat status bernama `GraphMethod AI Review`:

- `pending` ketika worker mulai memproses dan selama retry analisis.
- `success` setelah publikasi dan skor memenuhi threshold.
- `failure` jika skor di bawah threshold.
- `error` jika seluruh `WORKER_MAX_ATTEMPTS` gagal.

Supaya tombol merge benar-benar tertahan, jadikan status tersebut wajib pada target
branch. Setelah GraphMethod pernah membuat status pada sebuah PR, buka repository
**Settings → Rules → Rulesets** (atau **Branches → Branch protection rule**), aktifkan
**Require status checks to pass**, lalu pilih `GraphMethod AI Review`. Aktifkan juga
aturan agar branch harus up to date jika kebijakan repository membutuhkannya.

Check Run `GraphMethod Quality Gate` juga diterbitkan; jadikan check ini wajib
untuk instalasi baru. Komentar `/review` dari kolaborator pada PR memicu review ulang.

Tanpa ruleset/branch protection, status tetap terlihat loading tetapi GitHub masih
mengizinkan user yang memiliki akses untuk merge.

### 15.2 Sticky summary

Bot membuat satu comment dengan marker internal:

```html
<!-- graphmethod-ai-review-summary -->
```

Comment yang sama diperbarui ketika review berikutnya selesai.

Isi summary dibagi menjadi empat bagian:

```markdown
## Summary

Ringkasan tujuan dan pendekatan perubahan PR.

## PR Detail

- Review scope: `<base SHA>` → `<head SHA>`
- Reviewed PR at head: `<head SHA>`
- Files reviewed: 2

| File | Change |
|---|---|
| `src/app.py` | Penjelasan singkat perubahan file. |
| `tests/test_app.py` | Penjelasan singkat perubahan pengujian. |

## Impact

Dampak perubahan terhadap flow, logic, dependency, dan compatibility existing code.

## Risk

| Level | Findings |
|---|---:|
| Critical | 0 |
| High | 0 |
| Medium | 1 |
| Low | 0 |

### Findings

| File | Risk | Finding | Conflict with | Details |
|---|---|---|---|---|
| `src/app.py` | Medium | Contract mismatch | `src/worker.py::run` | Caller masih mengharapkan contract lama sehingga flow dapat gagal. |
```

Path pada tabel divalidasi terhadap diff PR. Jika Gemini tidak mengembalikan deskripsi
untuk suatu file, bot tetap menampilkan file tersebut dengan jumlah baris tambah/hapus.
Jika finding merupakan conflict dengan existing code, tabel dan inline comment menjelaskan
target yang berbenturan serta konsekuensi konkretnya. Field conflict tidak digunakan untuk
Git merge conflict karena informasi tersebut tidak dapat dibuktikan dari diff dan graph context.

### 15.3 Inline comments

Finding dengan path dan changed line valid dipublikasikan di tab **Files changed**. Semua finding dikirim sebagai satu Pull Request Review untuk mengurangi notifikasi.

### 15.4 Suggested change

Jika model memberikan exact replacement code dan line range valid, bot membentuk:

````markdown
```suggestion
replacement_code()
```
````

Kode repository tidak berubah sampai developer memilih untuk menerapkan suggestion tersebut.

### 15.5 Finding non-inline

Finding lintas file atau finding dengan line yang tidak dapat diverifikasi dimasukkan ke summary dan tidak dipaksakan menjadi inline comment.

## 16. Melihat data Neo4j

Buka Neo4j Browser dan jalankan:

```cypher
MATCH (repository:Repository)-[:HAS_SNAPSHOT]->(snapshot:Snapshot)
RETURN repository, snapshot
LIMIT 25;
```

Melihat symbol dan relationship:

```cypher
MATCH (source:SymbolVersion)-[relationship]->(target:SymbolVersion)
RETURN source, relationship, target
LIMIT 100;
```

Melihat context untuk symbol tertentu:

```cypher
MATCH path = (source:SymbolVersion)-[:CALLS|IMPLEMENTS|EXTENDS|REFERENCES|TESTS*1..2]-(target)
WHERE source.qualified_name = "PaymentService.create_payment"
RETURN path
LIMIT 25;
```

## 17. Troubleshooting

### API mengembalikan 401 pada webhook

Kemungkinan penyebab:

- `GITHUB_WEBHOOK_SECRET` berbeda dari GitHub App settings.
- Request body berubah sebelum signature diverifikasi.
- Header `X-Hub-Signature-256` tidak ada.

### Worker mendapatkan 401 atau 403 dari GitHub

Periksa:

- `GITHUB_APP_ID` benar.
- Private key berasal dari GitHub App yang sama.
- Base64 private key tidak terpotong.
- App sudah di-install pada repository.
- Permission `Contents: Read`, `Pull requests: Read and write`, dan
  `Commit statuses: Read and write` sudah disetujui.

### Gemini mengembalikan 429

Free tier memiliki rate limit. Worker akan melakukan retry dengan exponential backoff sampai `WORKER_MAX_ATTEMPTS`. Kurangi concurrency, ukuran PR, context budget, atau tunggu quota pulih.

### `/health/ready` mengembalikan 503

Periksa container:

```powershell
docker compose -f deploy/local/compose.yaml ps
docker compose -f deploy/local/compose.yaml logs postgres neo4j api
```

Pastikan hostname sesuai mode eksekusi:

- Dalam Compose: `postgres` dan `neo4j`.
- Python pada host: `localhost`.

### PR terlalu besar

Worker menolak PR yang melebihi `MAX_CHANGED_FILES` atau `MAX_CHANGED_LINES`. Naikkan batas secara hati-hati karena akan meningkatkan indexing, latency, dan token usage.

### Tidak ada graph context

Kemungkinan penyebab:

- PR hanya mengubah module-level code.
- Parser tidak berhasil menyelesaikan dynamic call.
- Target function memiliki nama ambigu.
- Repository tidak memiliki source Python, JavaScript/JSX, atau TypeScript/TSX.

Reviewer tetap dapat memproses diff, tetapi akan mencatat bahwa tidak ada additional graph context.

### Summary muncul tetapi inline comment tidak ada

Finding hanya menjadi inline comment jika path dan line benar-benar ada pada diff. Finding dengan lokasi invalid otomatis dipindahkan ke summary.

## 18. Security checklist

Sebelum mengaktifkan repository:

- Jangan commit `.env`.
- Jangan commit file `.pem`.
- Gunakan permission GitHub App minimum.
- Gunakan repository test non-sensitif untuk free-tier prototype.
- Jangan mengeksekusi source code PR.
- Jangan menulis raw source code ke log.
- Rotasi key jika pernah terekspos.
- Gunakan HTTPS untuk webhook.
- Gunakan secret manager ketika berpindah ke production.
- Uji isolation sebelum membuat GitHub App tersedia untuk banyak akun.

## 19. Mengganti LLM provider

Review pipeline bergantung pada interface `ReviewProvider`, bukan langsung pada Gemini. Adapter tersedia di:

```text
src/codereviewer/providers/
├── base.py
├── gemini.py
└── registry.py
```

Untuk menambahkan provider baru:

1. Implementasikan `ReviewProvider`.
2. Normalisasikan response menjadi `ReviewResult`.
3. Normalisasikan rate limit, timeout, dan invalid output.
4. Daftarkan provider di `registry.py`.
5. Tambahkan test adapter.
6. Ubah `LLM_PROVIDER` dan `LLM_MODEL`.

GitHub integration, graph retrieval, validation, dan publishing tidak perlu ditulis ulang.

## 20. Batasan prototype

- Tree-sitter memparse Python, JavaScript/JSX, dan TypeScript/TSX.
- Name-based call resolution bersifat konservatif dan tidak dapat menyelesaikan seluruh dynamic dispatch.
- Repository di-index melalui GitHub API dan ditujukan untuk repository prototype kecil sampai menengah.
- Upgrade schema SRS PostgreSQL tersedia saat startup; hentikan worker versi lama
  sebelum upgrade dan ikuti [prosedur upgrade](docs/06-srs-implementation.md).
- Check Runs, `/review`, konfigurasi repo dan quality gate sudah tersedia.
- Tree repository yang truncated atau diff di atas budget ditolak; komentar GitHub dipaginasi.
- Live integration memerlukan valid GitHub App, Gemini, PostgreSQL, dan Neo4j credentials.

## 21. Perintah cepat

```powershell
# Persiapan pertama
Copy-Item .env.example .env

# Jalankan seluruh stack
docker compose -f deploy/local/compose.yaml up --build -d

# Periksa status
docker compose -f deploy/local/compose.yaml ps

# Lihat log
docker compose -f deploy/local/compose.yaml logs -f api worker

# Jalankan test
.\.venv\Scripts\python.exe -m pytest -q

# Jalankan lint
.\.venv\Scripts\python.exe -m ruff check .

# Hentikan tanpa menghapus data
docker compose -f deploy/local/compose.yaml down
```


