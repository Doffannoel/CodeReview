# Prompt dan integrasi Gemini

## Dari data menjadi prompt

Prompt dibuat oleh `build_prompt()` di `review.py` dari metadata PR, parsed unified diff, context graph terpilih, dan `CONTEXT_TOKEN_BUDGET`.

```text
[SYSTEM INSTRUCTION]
<aturan reviewer dan perlakuan untrusted data>

[PULL REQUEST]
Title: ...
Description: ...
Base SHA: ...
Head SHA: ...

[DIFF]
<unified diff, dipotong bila melebihi budget>

[STRUCTURAL GRAPH CONTEXT]
Context ID: ...
Path: ...
Symbol: ...
Relation: ...; graph distance: ...
<source simbol>
---
<context berikutnya>

[OUTPUT]
Return a summary, one file review per changed file, impact analysis, and findings.
For demonstrable logic or compatibility conflicts, identify the exact existing
file, symbol, configuration, API, contract, or flow that conflicts.
```

Instruksi meminta model memperlakukan isi repository/PR sebagai untrusted data, hanya mereview diff/context yang diberikan, fokus pada correctness/security/reliability/concurrency/maintainability, menghindari style-only feedback, dan tidak membuat finding tanpa bukti.

## Budget

Diff mendapat 65% `CONTEXT_TOKEN_BUDGET`, minimal 1.000 estimated tokens. `trim_to_tokens()` memakai estimasi empat karakter per token dan memotong dari akhir. Context graph sebelumnya sudah dipilih dengan budget tersendiri. Metadata dan instruksi tidak dihitung eksplisit.

## Request Gemini

`GeminiReviewProvider` mengirim HTTP POST melalui `httpx.AsyncClient` dengan timeout 90 detik:

```text
POST {LLM_BASE_URL}/models/{LLM_MODEL}:generateContent
X-Goog-Api-Key: {LLM_API_KEY}
```

Payload konseptual:

```json
{
  "contents": [
    {
      "role": "user",
      "parts": [{"text": "<prompt lengkap>"}]
    }
  ],
  "generationConfig": {
    "responseMimeType": "application/json",
    "responseJsonSchema": "<schema ModelReviewResponse>"
  }
}
```

Walau prompt memiliki bagian `[SYSTEM INSTRUCTION]`, implementasi mengirim semuanya sebagai satu content dengan role `user`; adapter belum memakai field system instruction terpisah dari API Gemini.

MIME type dan JSON Schema meminta structured output agar parsing tidak bergantung pada Markdown/regex. Parameter sampling `temperature`, `top_p`, dan `top_k` sengaja tidak dikirim karena sudah deprecated untuk Gemini 3.8 Flash.

## Schema response

```json
{
  "summary": "ringkasan review",
  "file_reviews": [
    {
      "path": "src/example.py",
      "change_summary": "ringkasan perubahan file"
    }
  ],
  "impact": "dampak terhadap flow dan logic existing",
  "findings": [
    {
      "path": "src/example.py",
      "start_line": 42,
      "end_line": 42,
      "severity": "high",
      "category": "correctness",
      "title": "judul singkat",
      "explanation": "bukti dan dampak",
      "conflict": {
        "target": "src/caller.py::existing_contract",
        "description": "bagian yang tidak kompatibel dan konsekuensinya"
      },
      "suggestion": "exact replacement code atau null",
      "confidence": 0.9,
      "supporting_context_ids": ["<stable symbol id>"]
    }
  ]
}
```

Aturan Pydantic:

- severity hanya `low`, `medium`, `high`, atau `critical`;
- confidence berada di 0–1;
- suggestion adalah exact replacement tanpa Markdown fence atau `null`;
- conflict berisi target existing code/contract dan penjelasan incompatibility, atau `null`;
- conflict bukan deteksi Git merge conflict;
- findings boleh kosong;
- `supporting_context_ids` tersedia di schema, tetapi saat ini belum divalidasi terhadap context atau disimpan ke tabel finding.

`MAX_FINDINGS` tidak dikirim sebagai angka literal. Prompt hanya menyebut “configured number”; batas yang benar-benar enforced adalah slicing `result.findings[:max_findings]` setelah model menjawab.

## Parsing response

Adapter mengambil `payload.candidates[0].content.parts[0].text`, menjalankan `json.loads`, lalu memvalidasi ulang dengan `ModelReviewResponse.model_validate()`. `promptTokenCount` dan `candidatesTokenCount` menjadi input/output token metrics pada `ReviewResult`.

Structured output memastikan bentuk data, bukan kebenaran klaim. Orkestrator kemudian membatasi jumlah, menormalisasi path, memverifikasi changed line/range, menghapus suggestion yang tidak bisa inline, dan mendeduplikasi fingerprint.

## Error dan retry

| Kondisi | Error | Dampak |
|---|---|---|
| API key kosong | `ProviderError` | Run gagal; retry oleh worker. |
| HTTP 408, 429, atau 5xx | retry internal provider | Exponential backoff dengan jitter hingga `LLM_MAX_RETRIES`. |
| HTTP 429 setelah retry habis | `ProviderRateLimited` | Diteruskan ke retry job bila attempt tersisa. |
| HTTP error lain | `ProviderError` | Diteruskan tanpa retry internal. |
| Candidate/text hilang | `InvalidProviderOutput` | Run gagal dan dapat di-retry. |
| JSON/schema invalid | `InvalidProviderOutput` | Run gagal dan dapat di-retry. |
| Timeout/network | exception `httpx` | Naik ke worker dan dapat di-retry. |

Provider melakukan retry dekat dengan request untuk error transien 408, 429, dan 5xx. Default-nya empat retry dengan jeda dasar satu detik, exponential backoff, batas 60 detik, dan jitter. Jika model utama tetap mengembalikan HTTP 503, provider mencoba `LLM_FALLBACK_MODEL`; model yang benar-benar digunakan dicatat pada metadata. Jika keduanya gagal, queue masih dapat mengulang keseluruhan job dengan `available_at = now + 2 ** attempt` hingga `WORKER_MAX_ATTEMPTS`.

## Contoh query yang disederhanakan

```text
[SYSTEM INSTRUCTION]
You are a pull request code reviewer...

[PULL REQUEST]
Title: Prevent duplicate payment capture
Description:
Add idempotency handling to payment service.
Base SHA: abc...
Head SHA: def...

[DIFF]
diff --git a/payment.py b/payment.py
...

[STRUCTURAL GRAPH CONTEXT]
Context ID: 123:payment.py:PaymentService.capture
Path: payment.py
Symbol: PaymentService.capture
Relation: CHANGED_SYMBOL; graph distance: 0
async def capture(...): ...

---

Context ID: 123:worker.py:retry_payment
Path: worker.py
Symbol: retry_payment
Relation: CALLS; graph distance: 1
async def retry_payment(...): ...

[OUTPUT]
Return a summary, one file review per changed file, impact analysis, and findings.
Use repository-relative paths and new-file line numbers. Populate conflict only
when the supplied diff and graph context demonstrate an incompatibility.
```

Contoh hanya menunjukkan format. Isi produksi berasal dari webhook, GitHub diff, dan snapshot graph untuk head SHA aktual.
