# Provenance Guard

A backend that a creative-sharing platform could plug into to classify submitted text as likely AI-generated, likely human-written, or uncertain. It returns a confidence score and a plain-language transparency label, lets creators appeal, and records every decision in a structured audit log.

The design is in [planning.md](planning.md). That file also has the architecture diagram.

## Setup and running

```bash
python -m venv .venv
source .venv/Scripts/activate      # Git Bash   (PowerShell: .venv\Scripts\activate)
pip install -r requirements.txt
```

Create a `.env` file (it is gitignored) with:

```text
GROQ_API_KEY=your_key_here
# optional: GROQ_MODEL=openai/gpt-oss-20b
```

Run with `python app.py`, which serves on <http://localhost:5000>. Data is stored in `provenance.db` (SQLite, gitignored).

**Model note:** the assignment suggests `meta-llama/llama-4-scout-17b-16e-instruct`, but Groq returns `model_not_found` for it on my account. I use `openai/gpt-oss-20b` instead. It is configurable through `GROQ_MODEL`.

## API

| Endpoint | Body | Returns |
| --- | --- | --- |
| `POST /submit` | `{creator_id, text}` (text of at least 25 words) | `201` with `content_id`, `attribution`, `confidence`, `label`, `scores`, `signals`, `status`. `400` on bad input, `429` when rate limited. |
| `POST /appeal` | `{content_id, creator_reasoning, creator_id?}` | `200` with `{content_id, appeal_id, status: "under_review"}`. `400` missing field, `403` wrong creator, `404` unknown content, `409` appeal already open. |
| `GET /content/<id>` | none | Status, current label, original decision, and appeal (if any). |
| `GET /log` | none | Audit entries, newest first. |

PowerShell users can send requests with `Invoke-RestMethod -Method Post -Uri http://localhost:5000/submit -ContentType "application/json" -Body '{"text": "...", "creator_id": "me"}'`.

## Architecture

A submission is rate-limited and validated, then scored separately by two independent signals. A scorer combines them into a verdict and confidence, a label is chosen, and the decision is written to SQLite and the audit log. An appeal looks up the original decision, flips the content's status to `under_review`, stores the creator's reasoning, and logs it next to the decision it contests. The full diagrams are in [planning.md](planning.md#architecture).

```text
POST /submit -> rate limit -> validate -> [LLM signal] + [stylometric signal]
             -> scorer (ai_score, agreement, verdict) -> label -> SQLite + audit log -> response

POST /appeal -> validate + look up decision -> status = under_review -> appeals + audit log -> response
```

Files: `app.py` (routes), `signals.py` (both signals), `scoring.py` (combining), `labels.py` (label text), `db.py` (storage and log).

## Detection signals

I used two signals that look at different things, so their mistakes are unlikely to line up.

**Signal 1: LLM judgment (Groq).** The model reads the text and returns `{score 0-1, reason}`, where 1 means AI. It captures meaning and voice: generic phrasing, stacked transitions ("Furthermore", "In conclusion"), and a lack of concrete detail. Blind spots: it is not calibrated and it is sensitive to prompt wording. It over-flags polished, formal, or non-native-English prose, and it is fooled by lightly edited AI text.

**Signal 2: Stylometry (pure Python).** Four structural metrics, each scaled to 0-1 and averaged:

- Sentence-length variation (AI is more uniform).
- Type-token ratio (vocabulary diversity).
- Punctuation variety (`? ! ; : ( ) - ...` per sentence).
- Closeness of average sentence length to about 21 words.

It ignores meaning entirely. Blind spots: it is noisy on short texts, and poetry or minimalist prose looks "uniform" to it. Formal writing has low variance too.

If Groq fails, the verdict is forced to `uncertain` and the label notes that the check was incomplete. The system never gives a confident verdict from one signal.

## Confidence scoring

**Combination.** `ai_score = 0.6 * llm + 0.4 * stylometric`, and `agreement = 1 - |llm - stylometric|`. The LLM gets more weight because it sees meaning. Stylometry is the check on it.

**What a score means.** `ai_score` is evidence strength, not a probability. 0.5 means the signals give no usable lean. A score only becomes an accusation when it is high and both signals agree.

| Verdict | Rule |
|---|---|
| `likely_ai` | `ai_score >= 0.80` and `agreement >= 0.65` and at least 50 words |
| `likely_human` | `ai_score <= 0.40` |
| `uncertain` | everything else |

The asymmetry is deliberate: wrongly accusing a human is worse than missing an AI text, so `likely_ai` has a high bar and a human verdict has a low one. Reported `confidence` is `ai_score` for AI, `1 - ai_score` for human, and `1 - 2*|ai_score - 0.5|` for uncertain (highest when the system most clearly can't tell).

**Two example submissions** (real outputs):

| | LLM | Stylometric | `ai_score` | Agreement | Verdict | Confidence |
|---|---|---|---|---|---|---|
| Stacked-transition AI paragraph | 0.93 | 0.67 | **0.825** | 0.74 | `likely_ai` | 0.825 |
| Formal paragraph on monetary policy (human-style) | 0.70 | 0.82 | **0.748** | 0.88 | `uncertain` | 0.504 |
| Casual ramen review | 0.08 | 0.33 | **0.18** | 0.75 | `likely_human` | 0.82 |

**How I tested whether the scores are meaningful.** I ran about eight hand-picked inputs: clear AI, clear human, formal human, lightly edited AI, a short descriptive passage, and a poem. I printed both signal scores separately to see which one moved. Findings:

- The scores spread across all three bands and the edge cases landed in `uncertain` or `likely_human`, not `likely_ai`.
- The first version could not produce `likely_ai`. The LLM capped at 0.8 and stylometry hovered around 0.6, so the combined score never reached 0.80. I fixed this by adding scoring anchors to the LLM prompt (for example, "0.9-0.97 = obviously AI"). I did not lower the threshold, because that would have flagged the formal human paragraph.
- The LLM is prompt-sensitive: the same short descriptive passage went from 0.8 to 0.1 after that prompt change. That is why I keep the stylometric check and the agreement rule.
- This is a small sample, so the thresholds are untuned. I did not want to overfit them.

## Transparency label variants

Shown to readers beside the content. No percentages are shown to readers; those are in the API response and log.

| Variant | Exact text |
|---|---|
| High-confidence AI | "🤖 Likely AI-generated. Our automated check found strong signs that this was written with AI tools. This is an estimate, not a certainty. If you wrote this yourself, the creator can request a review." |
| High-confidence human | "✍️ Likely human-written. Our automated check found no strong signs of AI generation. No tool can be fully certain, but this looks like original human work." |
| Uncertain | "❓ Origin unclear. Our automated check could not tell whether this was written by a person or with AI tools. No conclusion should be drawn about the creator." |
| Under review (after an appeal) | "🔍 Under review. The creator has asked for this label to be reviewed. A person is taking a second look." |

## Appeals

A creator submits `content_id` and `creator_reasoning`. The system sets the content's status to `under_review`, stores the appeal, and writes an `appeal` entry to the audit log that includes the reasoning and the original verdict and scores. The label switches to the "Under review" text. A second appeal on the same content returns `409`. There is no automated re-classification. A human reviewer would read the log entry, which has everything needed to decide.

`creator_id` is optional on appeals and is only checked if supplied, because this project has no real authentication.

## Rate limiting

| Endpoint | Limit |
|---|---|
| `POST /submit` | 5 per minute; 50 per day per client IP |
| `POST /appeal` | 10 per hour per IP |

**Reasoning.** A real creator posts a handful of pieces a day. 5 per minute leaves room for a burst of re-submits while editing, and 50 per day is far more than a real writer needs. An attacker flooding the endpoint, or probing for text that slips past the detector, hits the limit quickly. It also protects the free Groq quota, since every submission costs one LLM call. The limiter runs before any detection. Appeals are rarer, so 10 per hour is generous.

**Evidence.** 12 rapid requests to `/submit` (status codes):

```text
201
201
201
201
201
429
429
429
429
429
429
429
```

The `429` body is `{"error": "rate limit exceeded", "limit": "5 per 1 minute"}`. Storage is in-memory, so limits reset when the server restarts, and they would need Redis in production.

## Audit log

Every decision and every appeal is written to a structured `audit_log` table, and `GET /log` returns it as JSON. Each entry has `appeal_filed`, so you can see whether a decision was contested. Sample from `GET /log` (newest first; the appeal and the decision it contests are for the same `content_id`; the label text is omitted here for space and is in the real log):

```json
{
  "entries": [
    {
      "timestamp": "2026-10-05T16:15:22.987Z",
      "event": "appeal",
      "content_id": "2f54ab6c-7930-40c8-9171-e6248f6b279f",
      "creator_id": "u-formal",
      "appeal_id": "c47d23b7-9ffd-413b-9b2a-4702884ba603",
      "appeal_reasoning": "I wrote this myself; I'm a non-native English speaker and my writing is formal.",
      "status": "under_review",
      "original_attribution": "uncertain",
      "original_confidence": 0.504,
      "llm_score": 0.7,
      "stylometric_score": 0.82,
      "appeal_filed": true
    },
    {
      "timestamp": "2026-10-05T16:15:14.778Z",
      "event": "decision",
      "content_id": "2f54ab6c-7930-40c8-9171-e6248f6b279f",
      "creator_id": "u-formal",
      "attribution": "uncertain",
      "confidence": 0.504,
      "ai_score": 0.748,
      "agreement": 0.88,
      "llm_score": 0.7,
      "stylometric_score": 0.82,
      "signals_used": ["llm", "stylometric"],
      "status": "classified",
      "appeal_filed": true
    },
    {
      "timestamp": "2026-10-05T16:15:11.512Z",
      "event": "decision",
      "content_id": "bad2f1c1-1a95-4f68-892c-bffdad43be49",
      "creator_id": "u-human",
      "attribution": "likely_human",
      "confidence": 0.82,
      "ai_score": 0.18,
      "agreement": 0.749,
      "llm_score": 0.08,
      "stylometric_score": 0.331,
      "signals_used": ["llm", "stylometric"],
      "status": "classified",
      "appeal_filed": false
    },
    {
      "timestamp": "2026-10-05T16:15:08.988Z",
      "event": "decision",
      "content_id": "f00c32f2-1863-4b47-a30a-f23c07f858e2",
      "creator_id": "u-ai",
      "attribution": "likely_ai",
      "confidence": 0.825,
      "ai_score": 0.825,
      "agreement": 0.738,
      "llm_score": 0.93,
      "stylometric_score": 0.668,
      "signals_used": ["llm", "stylometric"],
      "status": "classified",
      "appeal_filed": false
    }
  ]
}
```

## Known limitations

- **Formal or academic prose and non-native-English writing.** The LLM reads polished, conventional phrasing as AI, and stylometry sees low sentence-length variation and little informal punctuation. Both signals can lean the same way, so they agree on the wrong answer. In testing, a formal human paragraph scored 0.748, just below the 0.80 bar. A longer or slightly more uniform one could cross it, and the agreement check would not help because the signals would agree. The appeal path is the safety net.
- **Poetry and minimalist or repetitive writing.** Short, regular lines give stylometry low variance, which it reads as AI. The LLM is the counterweight, and the disagreement rule pushes these to `uncertain`, but that is a mitigation, not a fix.
- **Lightly edited AI text** came out `likely_human` in testing. Edits add punctuation variety and break up sentence rhythm, which are the things stylometry measures. I accept this because it is the cheaper error.
- **Short texts.** Under 25 words are rejected, and under 50 words can never be `likely_ai`. Stylometric statistics on a few sentences are mostly noise.
- **Small evaluation.** About eight hand-picked samples, so the thresholds are untuned and not validated.

**If this were deployed for real,** I would calibrate the thresholds on a labeled set of human and AI writing from the platform's own users, add per-user history as a third signal, store limits in Redis, and add real authentication for appeals and the log.

## Spec reflection

**Where the spec helped.** Writing the thresholds, the agreement rule, and the false-positive scenario before any code meant that scoring was a straight implementation. When the LLM score saturated at 0.8, I could tell exactly what was wrong because my spec said what `likely_ai` should require. The spec also made me decide up front that the verdict should be `uncertain` when the signals disagree, which is what protects the poem and the formal-writing cases.

**Where the implementation diverged.**

- The spec used the assignment's suggested Llama model. It was unavailable on my account, so I switched to `gpt-oss-20b`.
- The spec called for rejecting texts under 50 words. The assignment's own sample text is 28 words, so I lowered the rejection limit to 25 and kept 50 as the limit for `likely_ai`.
- The spec's appeal field was `reasoning`. I renamed it `creator_reasoning` to match the assignment, and made `creator_id` optional because the assignment's appeal example doesn't send it.
- My spec assumed the LLM would use the full 0-1 range. It didn't, which is why I added prompt anchors.

## AI usage

I used Claude Code throughout. Concrete instances:

1. **The first signal and the Groq model.** I had Claude generate the Flask skeleton and `llm_signal()` from my planning.md. The first version used the assignment's Llama model and silently returned `None` for every input, because the function swallows errors. Claude diagnosed it by listing the models my key could access, and I switched to `openai/gpt-oss-20b` and made it configurable. I also updated planning.md so the spec matched the code.
2. **Scoring calibration.** Claude implemented `score_content()` from my uncertainty section, and I checked it against my thresholds. Testing showed `likely_ai` was unreachable. The suggested fix, lowering the threshold, would have flagged the formal human paragraph, so I went with a prompt change instead (scoring anchors for the LLM). I re-ran the same samples before and after to confirm the change.
3. **Conflicts between the assignment's test commands and my spec.** The assignment's sample inputs would have failed my 50-word minimum, and its rate-limit test assumed 10 per minute. I decided what to change (25-word minimum, keeping my 5 per minute) and documented it here.

## Stretch features

None implemented.
