# Provenance Guard — planning.md

Status: **Milestone 2 complete** (spec written before any code). Numbers below are starting values; any change after testing gets noted in the README.

## Architecture narrative

A creative-sharing platform sends a piece of text to `POST /submit`. The path it takes:

1. **Flask API (`app.py`)** receives `{creator_id, text}`. **Flask-Limiter** checks the per-client rate limit first; over-limit requests get a `429` and never reach detection (this protects the Groq quota).
2. **Input validation** rejects empty text or text that is too short to analyze (stylometry is meaningless on a few words).
3. A `content_id` is generated and the submission is stored with status `classified`.
4. **Signal 1 — Groq LLM classifier** gets the raw text and returns an `ai_likelihood` in [0, 1] plus a short rationale.
5. **Signal 2 — Stylometric heuristics** get the same raw text and return their own `ai_likelihood` in [0, 1], computed in pure Python.
6. **Confidence scorer** combines the two scores into one `ai_score` and an `agreement` measure. It maps these to a verdict: `likely_ai`, `likely_human`, or `uncertain`.
7. **Label generator** turns the verdict and confidence into plain-language transparency label text.
8. **Audit log (SQLite)** records the decision: content_id, timestamp, both signal scores, combined score, verdict, and label.
9. The API returns `{content_id, attribution, confidence, label, signals}`.

If a creator disagrees, `POST /appeal` takes `{content_id, reasoning}`. The system looks up the original decision, sets the content's status to `under_review`, writes an appeal record linked to the original decision, appends an audit-log entry, and returns confirmation. `GET /log` and `GET /content/<id>` read from the same store.

## Detection signals

### Signal 1 — LLM classification (Groq, `openai/gpt-oss-20b`)

*Model note: the assignment suggests `meta-llama/llama-4-scout-17b-16e-instruct`, but Groq returns `model_not_found` for it on my account. I switched to `openai/gpt-oss-20b`, which is configurable through the `GROQ_MODEL` env var.*

- **Measures:** holistic semantic and stylistic impression. The model is prompted to return JSON with a 0–1 AI-likelihood and one-sentence reasoning.
- **Why it differs:** AI text tends toward generic phrasing, safe word choice, tidy structure, balanced "on one hand / on the other" framing, and few idiosyncratic specifics. Human text often has odd detail, voice, and imperfection.
- **Blind spots:** LLM judges are overconfident and biased. They flag polished, formal, or non-native-English writing as AI. They are easily fooled by lightly edited AI text. The score is not calibrated and varies between runs, so I use temperature 0.
- **Failure handling:** if the Groq call fails or returns unparseable output, the signal reports `unavailable` and the system falls back to a capped, lower-confidence result (never a confident verdict from one signal).

### Signal 2 — Stylometric heuristics (pure Python)

- **Measures:** structural statistics:
  - sentence-length variance (burstiness)
  - type-token ratio (vocabulary diversity)
  - punctuation density and variety
  - average sentence length
- **Why it differs:** AI text is more uniform, with similar sentence lengths and steady rhythm. Human writing is "burstier", with short fragments next to long runs.
- **Blind spots:** it ignores meaning entirely. Short texts give noisy statistics. Poetry and deliberately minimalist or repetitive styles look "uniform" and can be flagged wrongly. Formal writing such as academic prose has low variance too. Heavily edited AI text can add burstiness.
- **Output:** each metric is normalized to 0–1 against rough human/AI reference ranges, then averaged into one `ai_likelihood`.

### Why they are independent

Signal 1 reads *what the text says and how it sounds*. Signal 2 only counts *shape*. Their errors are mostly uncorrelated: a formal essay may fool one and not the other. Disagreement between them is itself useful information.

### Signal outputs

| Signal | Output |
| - | - |
| LLM | `{"score": float 0-1, "reason": str}`; 0 = clearly human, 1 = clearly AI. Temperature 0. |
| Stylometric | `{"score": float 0-1, "metrics": {burstiness, ttr, punct_density, avg_sentence_len}}` |

## Uncertainty representation

- `ai_score = 0.6 * llm + 0.4 * stylometric`. The LLM gets more weight because it sees meaning, but stylometry keeps it honest.
- `agreement = 1 - |llm - stylometric|` (1 = identical, 0 = opposite).
- **What a score means:** `ai_score` is *evidence strength*, not a probability. 0.5 means "the signals give no usable lean", and 0.6 means "a slight lean toward AI that is not strong enough to say anything public". A score only becomes an accusation when it is high **and** both signals agree.
- **Asymmetry:** a false positive is worse than a false negative, so the bar for `likely_ai` is much higher than the bar for `likely_human`.

| Verdict | Rule (checked in this order) |
|---|---|
| `likely_ai` | `ai_score >= 0.80` **and** `agreement >= 0.65` |
| `likely_human` | `ai_score <= 0.40` |
| `uncertain` | everything else, including any high score where the signals disagree |

- **Reported `confidence`:** for `likely_ai` it is `ai_score`. For `likely_human` it is `1 - ai_score`. For `uncertain` it is `1 - 2*|ai_score - 0.5|`, which is highest at 0.5, so it reads as "how sure we are that we can't tell".
- **Groq unavailable:** the verdict is forced to `uncertain`, `signals.llm` is `null`, and the label gets the note "Automated check was incomplete."
- **How I'll test it (README):** run about 5 clearly AI samples, 5 clearly human samples, and a few edge cases (poem, formal essay, edited AI text). I'll check that scores spread across the bands and that edge cases land in `uncertain`, not `likely_ai`.

## False positive scenario

A human poet submits a spare, repetitive poem with short regular lines.

1. Stylometry sees low sentence-length variance and a low type-token ratio, so it scores high (e.g. 0.75).
2. The LLM finds a distinct voice and scores low (e.g. 0.30).
3. Combined `ai_score` is about 0.57 and agreement is low, so the verdict is **uncertain**, not `likely_ai`. The higher bar for `likely_ai` and the disagreement rule exist to catch exactly this case.
4. The label says the system could not determine origin and does *not* accuse the creator.
5. If both signals did wrongly agree and the label said "likely AI", the label still says it is an automated estimate and links to the appeal path. The creator calls `POST /appeal` with their reasoning, such as drafts or process. Status becomes `under_review`, the appeal is logged next to the original decision, and a human reviewer can resolve it.

## Transparency label variants

Shown to readers next to the content. Plain language, no jargon, and no accusation unless the evidence is strong.

| Variant | Exact text |
|---|---|
| High-confidence AI (`likely_ai`) | "🤖 **Likely AI-generated.** Our automated check found strong signs that this was written with AI tools. This is an estimate, not a certainty. If you wrote this yourself, the creator can request a review." |
| High-confidence human (`likely_human`) | "✍️ **Likely human-written.** Our automated check found no strong signs of AI generation. No tool can be fully certain, but this looks like original human work." |
| Uncertain (`uncertain`) | "❓ **Origin unclear.** Our automated check could not tell whether this was written by a person or with AI tools. No conclusion should be drawn about the creator." |

Under review (after an appeal), the label is replaced by: "🔍 **Under review.** The creator has asked for this label to be reviewed. A person is taking a second look."

Design notes:
- No percentages are shown to readers; they are in the API response and the audit log only.
- The uncertain label tells readers explicitly *not* to assume AI, which is where the false positive protection shows up for readers.
- I'll show these to one person who hasn't seen the project before building the UI, and revise the wording if they misread it.

## Appeals workflow

- **Who:** the creator of the content, identified by the `creator_id` that matches the original submission. (No real auth in this project, so this is a simple match check.)
- **What they provide:** `content_id` and `reasoning` (required, free text, e.g. "I have my drafts and notes" or "this is my minimalist style").
- **What the system does:**
  1. Looks up the content and its original decision (404 if missing).
  2. Rejects if `creator_id` doesn't match (403), if reasoning is empty (400), or if an appeal is already open (409).
  3. Sets the content's status `classified` → `under_review`.
  4. Writes an `appeals` row linked to the original decision (`decision_id`).
  5. Appends an audit-log entry of type `appeal` with the reasoning, the original verdict and scores, and a timestamp.
  6. Returns `{content_id, status: "under_review", appeal_id}`.
- **Reviewer queue (`GET /log` filtered to appeals):** each item shows the content text, original verdict, `ai_score`, both signal scores with the LLM's reason, the stylometric metrics, the creator's reasoning, and timestamps. Automated re-classification is out of scope; a human decision just resolves the status.

## Anticipated edge cases

1. **Spare, repetitive poetry** (short lines, repeated phrases, simple vocabulary). Stylometry reads low variance and low type-token ratio as AI. Mitigation: disagreement with the LLM pushes it to `uncertain`, and the README notes that poetry is a known weak area.
2. **Formal or academic writing, or writing by non-native English speakers.** Both signals can lean AI because the prose is polished, uniform and conventional. This is the highest-risk false positive, since the signals might wrongly *agree*. Mitigation: the high 0.80 bar, and the label wording plus the appeal path.
3. **AI text lightly edited by a human** (contractions added, a few sentences broken up). Burstiness goes up and the LLM score drops, so it will likely come out `uncertain` or `likely_human`. That is a false negative, which I accept as the cheaper error.
4. **Very short text.** Stylometric statistics are noisy. Submissions under 25 words are rejected with a 400, and texts under 50 words never get a `likely_ai` verdict.
5. **Appeal edge cases:** a repeat appeal on the same content (409, no duplicate), and an appeal on content whose label was `likely_human` (allowed, since anyone may dispute a label, but it's logged normally).

## Rate limiting

- `POST /submit`: **5 per minute and 50 per day per client IP** (Flask-Limiter).
- `POST /appeal`: 10 per hour per IP.
- **Reasoning:** a real creator posts a handful of pieces per day, and 5 per minute leaves room for a burst of re-submits while editing. An adversary flooding the endpoint, or probing for text that slips past the detector, would hit the limit fast. It also protects the free Groq quota. The limiter returns `429` before any detection runs.

## API surface (contract)

| Endpoint | Accepts | Returns |
|---|---|---|
| `POST /submit` | JSON `{creator_id: str, text: str}` | `201` `{content_id, attribution, confidence, label, signals: {llm, stylometric}, status}`. `400` on bad input, `429` when rate limited. |
| `POST /appeal` | JSON `{content_id: str, reasoning: str}` | `200` `{content_id, status: "under_review", appeal_id}`. `404` if the content is unknown, `400` if reasoning is empty, `409` if an appeal is already open. |
| `GET /content/<content_id>` | none | Current status, original decision, label, and any appeal. |
| `GET /log` | optional `?limit=` | Structured audit entries, newest first (decisions and appeals). |

Storage (SQLite): `content`, `decisions`, `appeals`, plus one `audit_log` table whose rows are JSON-friendly.

## Architecture

### Submission flow

```text
Client
  |  POST /submit {creator_id, text}
  v
[Flask-Limiter] --over limit--> 429
  |  raw text
  v
[Validation] --bad input--> 400
  |  raw text
  +----------------------------+
  v                            v
[Signal 1: Groq LLM]     [Signal 2: Stylometry]
  |  llm_score (0-1)           |  style_score (0-1)
  +------------+---------------+
               v
      [Confidence Scorer]
               |  ai_score, agreement, verdict
               v
      [Label Generator]
               |  label text
               v
      [SQLite: content + audit_log]
               |  decision record (scores, verdict, label, timestamp)
               v
Response {content_id, attribution, confidence, label, signals}
```

### Appeal flow

```text
Client
  |  POST /appeal {content_id, reasoning}
  v
[Validation + lookup original decision] --not found--> 404
  |  content_id, reasoning, original decision
  v
[Status update: classified -> under_review]
  |  appeal record linked to decision
  v
[SQLite: appeals + audit_log]
  |  appeal entry (reasoning, original verdict, timestamp)
  v
Response {content_id, status: "under_review", appeal_id}
```

**Narrative.** On submission, the text is rate-limited and validated, then scored independently by the Groq LLM and the stylometric function. The scorer combines the two scores into `ai_score` and `agreement`, picks a verdict by threshold, generates the matching label, writes the decision to SQLite and the audit log, and returns everything to the client. On appeal, the creator's reasoning is validated and stored against the original decision, the content's status becomes `under_review`, and an audit-log entry captures the appeal next to the decision it contests.

## AI Tool Plan

### M3: submission endpoint + first signal

- *Give the tool:* Detection signals (Signal 1), API surface, and the submission-flow diagram.
- *Ask for:* a Flask app skeleton with `POST /submit` (validation, a stub response) and a `llm_signal(text)` function that calls Groq, parses the JSON, and returns `{score, reason}` or `None` on failure. The key is loaded from `.env` via python-dotenv.
- *Verify:* call `llm_signal` directly from a Python shell on 3 to 4 inputs (obvious AI text, a personal human anecdote, an empty string, and a forced failure with a bad key) before wiring it into the endpoint. Then `curl` the endpoint.

### M4: second signal + confidence scoring

- *Give the tool:* Detection signals (both), Uncertainty representation, and the submission-flow diagram.
- *Ask for:* `stylometric_signal(text)` returning `{score, metrics}`, and `score_content(llm, style)` returning `{ai_score, agreement, verdict, confidence}`, using the exact weights and thresholds in the spec.
- *Verify:* run clearly AI vs. clearly human samples and check that scores differ meaningfully. Run the poem and formal-essay edge cases and confirm they land in `uncertain`. Test the Groq-unavailable path and write the results to the README.

### M5: production layer

- *Give the tool:* Transparency label variants, Appeals workflow, Rate limiting, the appeal-flow diagram, and the SQLite storage note.
- *Ask for:* `make_label(verdict)` using the verbatim label text, the `POST /appeal`, `GET /content/<id>` and `GET /log` endpoints, SQLite tables with the audit log, and Flask-Limiter with the chosen limits.
- *Verify:* submit samples that reach all three label variants. Submit an appeal and check the status changes to `under_review` and the audit log shows the decision and appeal together. Test the error cases (404, 403, 409) and trigger a `429`. Capture at least 3 `GET /log` entries for the README.

I'll read and understand every generated function before keeping it, and compare it against this spec rather than accepting it as-is.
