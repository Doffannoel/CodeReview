# Implementasi Master SRS 3.0.0

Analisis mengacu pada `MASTER_SRS_AI_CODE_REVIEWER.pdf`, empat halaman, versi
15 Oktober 2026. PDF digunakan sebagai spesifikasi produk. Tidak ada instruksi
di dalamnya yang dijalankan sebagai perintah untuk mengubah lingkungan.

## Pemetaan kebutuhan

| Kebutuhan | Kondisi sebelumnya | Implementasi saat ini |
|---|---|---|
| FR-01 ingestion | HMAC dan antrean ada, tetapi webhook menunggu GitHub status API | Webhook hanya memvalidasi dan menyimpan job; seluruh request GitHub berjalan di worker |
| FR-02 AST | `ast.parse` Python saja | Tree-sitter Python, JS/JSX, TS/TSX, MJS/CJS/MTS/CTS; node File, Function, Class; CALLS, EXTENDS, IMPLEMENTS, TESTS |
| FR-03 lintas file | Resolusi leaf name dan graph snapshot head saja | Resolusi import/alias relatif, namespace/CommonJS sederhana; snapshot head dan base untuk perubahan/penghapusan; caller dan test masuk kandidat prompt |
| FR-04 JSON | JSON Schema/Pydantic ada; confidence tidak difilter | Aturan SMP, system instruction Gemini terpisah, filter confidence minimal 0,8, validasi lokasi/rentang, deduplikasi sebelum skor |
| FR-05 publisher | Summary, inline dan suggestion sudah ada | Tabel kategori/severity/penalti di atas, skor/target, coverage, update summary milik App; pagination komentar; indentasi suggestion dipertahankan |
| FR-06 `/review` | Tidak ada | `issue_comment.created` pada PR dari OWNER/MEMBER/COLLABORATOR; tiap komentar baru menghasilkan job; pengiriman ulang tidak menggandakan job |
| FR-07 konfigurasi | Tidak ada | `.graphmethod.yml` pada base SHA, safe YAML + validasi ketat, ignore paths, bahasa, confidence, jumlah inline, threshold dan bobot |
| FR-08 quality gate | Selalu success setelah analisis | Skor berbobot, Check Run `GraphMethod Quality Gate` dan commit status lama `GraphMethod AI Review`; success/failure sesuai threshold |

SMP rules mencakup SQL injection, price tampering, double spending, IDOR, bulk
Prisma create, transaksi multi-tabel, penamaan berdasarkan tipe, dan guard clauses.
Deteksi berasal dari penalaran model dengan diff/konteks, bukan jaminan bahwa semua
kerentanan pasti ditemukan. Pengujian lokal memverifikasi pengiriman aturan dan
pengolahan output; akurasi model harus diukur pada pilot dengan temuan berlabel.

SRS menyebut Next.js/Tailwind, tetapi tidak merinci standar khusus keduanya.
Keduanya direview untuk masalah yang dapat dibuktikan; aturan internal tambahan
dari `standard-recomendation.docx` tidak diasumsikan karena file tersebut tidak diberikan.

## Keputusan untuk ketidakkonsistenan SRS

- Skor = `max(0, 100 - jumlah penalti setiap temuan tervalidasi)`. Default
  Critical 25, High 10, Medium 5, Low 1; ambang kelulusan 80, inklusif.
  Contoh SRS dengan dua Critical, dua High dan satu Medium menghasilkan 25,
  bukan 60 seperti yang ditulis dalam contoh. Perhitungan implementasi konsisten.
- Guard clause memakai High sesuai matriks kebutuhan bab 2; contoh tabel bab 4
  yang menyebut Medium tidak dipakai sebagai aturan.
- Batas `max_findings` hanya membatasi inline. Seluruh temuan valid tetap muncul
  pada summary, tersimpan dalam audit, dan mengurangi skor.
- Konfigurasi dibaca dari base SHA agar PR tidak bisa menurunkan threshold atau
  mengabaikan file miliknya sendiri. Perubahan konfigurasi berlaku setelah merge.
- GitHub tidak menyediakan pemindahan komentar ke posisi paling atas. Summary
  yang sama diperbarui in-place, dengan tabel risiko di bagian awal komentar.

## Mengaktifkan pada instalasi yang sudah ada

1. Pada GitHub App, aktifkan **Checks: Read and write** dan **Issues: Read**,
   pertahankan **Contents:
   Read**, **Pull requests: Read and write**, **Commit statuses: Read and write**.
   Setujui perubahan permission pada installation.
2. Subscribe **Pull request** dan **Issue comment**. Endpoint tetap
   `/webhooks/github`, dengan secret HMAC yang sama.
3. Salin `.graphmethod.example.yml` menjadi `.graphmethod.yml` pada repositori
   yang direview, lalu merge ke branch dasar. Tanpa file ini, default berlaku.
4. Deploy image baru untuk API dan worker bersama-sama. Hentikan worker lama saat
   upgrade. Startup PostgreSQL menambah `request_key`, memperluas unique constraint,
   dan membuat `review_evaluations`, tanpa menghapus job/run/finding lama. Migrasi
   diserialisasi dengan advisory lock dan aman dijalankan ulang.
5. Atur branch protection/ruleset untuk mewajibkan **GraphMethod Quality Gate**
   dari GitHub App ini. Konfigurasi proteksi branch tetap dilakukan administrator.
   Commit status lama tetap diterbitkan agar aturan proteksi lama tetap bekerja.
6. Buka PR lalu kirim komentar tepat `/review` dari akun kolaborator untuk mencoba
   review ulang. Komentar pada issue biasa, bot, pengguna luar, dan edit komentar
   tidak memicu job baru.

Index graph menggunakan identitas profil Tree-sitter dan ignore paths, sehingga
snapshot Python lama tidak dipakai sebagai snapshot multi-bahasa. Source PR tidak
di-import atau dijalankan. API dan worker harus memakai versi aplikasi yang sama.

## Pengujian yang dapat diulang

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -e '.[dev]'
.venv/Scripts/ruff check src tests
.venv/Scripts/python -m pytest -q
docker compose -f deploy/test/compose.yaml up -d --wait
$env:GRAPHMETHOD_INTEGRATION_TESTS = '1'
.venv/Scripts/python -m pytest -q
Remove-Item Env:GRAPHMETHOD_INTEGRATION_TESTS
docker compose -f deploy/test/compose.yaml down -v
```

Service test hanya memakai localhost port 15432/17687 dan password test. Test
PostgreSQL membuat database sementara dengan nama acak, menguji upgrade data lama
serta claim paralel, lalu menghapus database tersebut. Test Neo4j memakai repository
ID sementara dan membersihkan node miliknya. `.env` lokal tidak dibaca oleh test.
Request GitHub/Gemini di pengujian diganti transport/mock lokal; tidak mengirim
komentar ke PR sungguhan atau mengonsumsi kuota model.

Hasil verifikasi lokal, 18 Oktober 2026:

- 53 test lulus dengan PostgreSQL 16 dan Neo4j 5 sungguhan, termasuk migrasi
  data lama dan claim paralel. Tanpa service integrasi: 51 lulus, 2 dilewati.
- Linux Python 3.12 dalam Docker: 51 lulus, 2 test integrasi dilewati.
- Ruff dan `pip check` lulus. Ada satu peringatan deprecation TestClient dari
  dependensi Starlette/httpx; tidak menyebabkan kegagalan test.
- API container: `/health/live` dan `/health/ready` mengembalikan 200. Webhook
  HMAC masuk PostgreSQL dan merespons 202 dalam satu pengukuran sekitar 65 ms;
  pengiriman ulang dikenali sebagai duplikat. Ini bukan benchmark SLA produksi.
- Parser juga berhasil memproses 21 file source proyek sendiri, menghasilkan
  159 simbol dan 125 relasi.
- Build Docker lokal memerlukan CA trust store Windows yang dipasang sementara
  sebagai BuildKit secret karena CA lingkungan ini tidak tersedia di base image.
  Verifikasi TLS tetap aktif; Dockerfile produksi dan image tidak menyimpan CA tersebut.

## Batasan dan penerimaan operasional

- Target webhook <2 detik dan pilot ≤90 detik adalah sasaran pengukuran lingkungan
  SMP, bukan klaim yang dibuktikan oleh unit test lokal. Belum dilakukan pilot pada
  dua repo internal atau panggilan model/GitHub sungguhan.
- Pengunduhan source memakai maksimal delapan request paralel. Graph tetap dibatasi
  1–2 hop, jumlah node, ukuran file, dan token budget. Semua caller yang dapat
  diresolusikan menjadi kandidat dalam batas traversal; tidak semua kandidat selalu
  muat dalam prompt. Jumlah kandidat/terpilih tersedia di `review_evaluations`.
- Satu attempt dibatasi `WORKER_JOB_TIMEOUT_SECONDS` (default 90 detik). Retry dapat
  membuat waktu total lebih lama. Lease job yang ditinggalkan worker dipulihkan
  setelah timeout + 30 detik; jumlah attempt tetap dibatasi.
- Resolusi statis mendukung pola import umum. Dynamic dispatch/refleksi, re-export
  berlapis, alias tsconfig dan penerkaan tipe receiver belum menjadi type checker
  penuh. Target ambigu tidak dihubungkan. Jangan menganggap graph sebagai bukti
  lengkap seluruh jalur eksekusi.
- Diff terlalu besar/tidak lengkap, syntax source yang tidak dapat diparse, atau
  konfigurasi invalid membuat analisis gagal, bukan lulus dengan coverage palsu.
  Estimasi token tetap karakter/4; diff tidak dipotong diam-diam.
- Hasil model JSON yang tidak valid atau tidak selesai gagal dan mengikuti retry.
  PR yang sudah berganti head/ditutup saat analisis tidak dipublikasikan sebagai
  hasil terbaru. API GitHub tidak menyediakan transaksi atomik lintas pemeriksaan
  head dan penerbitan komentar, sehingga race yang sangat sempit tetap mungkin.

Rujukan implementasi: [Tree-sitter Python bindings](https://github.com/tree-sitter/py-tree-sitter),
[GitHub Check Runs](https://docs.github.com/en/rest/checks/runs),
[GitHub webhook events](https://docs.github.com/en/webhooks/webhook-events-and-payloads),
dan [Gemini generateContent](https://ai.google.dev/api/generate-content).
