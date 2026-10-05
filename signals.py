"""Detection signals for Provenance Guard."""
import json
import os
import re
import statistics

from dotenv import load_dotenv
from groq import Groq

load_dotenv()

# The assignment's suggested model (llama-4-scout) is not available on this Groq account.
MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")

SYSTEM_PROMPT = (
    "You are an expert at telling human-written text from AI-generated text. "
    "Judge the text for generic phrasing, safe word choice, tidy balanced structure, "
    "and lack of idiosyncratic detail (AI-like) versus distinct voice, odd specifics, "
    "and imperfection (human-like). Be cautious: polished, formal, or non-native English "
    "writing is NOT evidence of AI by itself. "
    "Use the full range with these anchors: 0.05-0.15 clearly human (casual, specific, messy); "
    "0.3-0.4 probably human; 0.5 genuinely cannot tell; 0.6-0.7 probably AI; "
    "0.9-0.97 obviously AI (stacked transition words like 'Furthermore', 'It is important to note', "
    "'In conclusion', generic abstractions, no concrete detail). "
    'Respond with JSON only: {"score": <number 0 to 1, 0 = clearly human, 1 = clearly AI>, '
    '"reason": "<one sentence>"}'
)

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _client


def llm_signal(text):
    """Signal 1: Groq LLM assessment.

    Returns {"score": float 0-1, "reason": str}, or None if the call fails or the
    response can't be parsed (callers must treat None as "signal unavailable").
    """
    try:
        resp = _get_client().chat.completions.create(
            model=MODEL,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Text to assess:\n\n{text}"},
            ],
        )
        data = json.loads(resp.choices[0].message.content)
        score = float(data["score"])
        if not 0.0 <= score <= 1.0:
            return None
        return {"score": round(score, 3), "reason": str(data.get("reason", ""))}
    except Exception:
        return None


def _scale(value, human_end, ai_end):
    """Linearly map value to 0 (at human_end) .. 1 (at ai_end), clamped."""
    t = (value - human_end) / (ai_end - human_end)
    return max(0.0, min(1.0, t))


def stylometric_signal(text):
    """Signal 2: structural statistics, no meaning involved.

    Returns {"score": float 0-1 (1 = AI-like), "metrics": {...}}.
    Four metrics, each scaled to 0-1 against rough human/AI reference ranges, then averaged:
      burstiness     coefficient of variation of sentence length (AI: uniform, low)
      ttr            type-token ratio (AI: slightly more repetitive/'safe' vocabulary)
      punct_variety  non-comma/period punctuation per sentence (humans use ?, !, -, (), ...)
      avg_sentence   closeness of mean sentence length to a ~21-word 'AI typical' length
    """
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n+", text.strip()) if s.strip()]
    lengths = [len(re.findall(r"[A-Za-z0-9']+", s)) for s in sentences]
    lengths = [n for n in lengths if n > 0]
    words = [w.lower() for w in re.findall(r"[A-Za-z0-9']+", text)]
    if len(lengths) < 1 or len(words) < 5:
        return {"score": 0.5, "metrics": {}}

    mean_len = statistics.mean(lengths)
    cv = statistics.pstdev(lengths) / mean_len if len(lengths) > 1 else 0.0
    ttr = len(set(words)) / len(words)
    odd_punct = len(re.findall(r"[?!;:()\-—–\"]|\.\.\.", text))
    punct_per_sentence = odd_punct / len(lengths)

    parts = {
        "burstiness": _scale(cv, human_end=0.6, ai_end=0.25),
        "ttr": _scale(ttr, human_end=0.95, ai_end=0.65),
        "punct_variety": _scale(punct_per_sentence, human_end=1.5, ai_end=0.0),
        "avg_sentence": _scale(abs(mean_len - 21), human_end=12, ai_end=2),
    }
    score = sum(parts.values()) / len(parts)
    return {
        "score": round(score, 3),
        "metrics": {
            "sentence_len_cv": round(cv, 3),
            "ttr": round(ttr, 3),
            "punct_per_sentence": round(punct_per_sentence, 3),
            "avg_sentence_len": round(mean_len, 1),
            "component_scores": {k: round(v, 3) for k, v in parts.items()},
        },
    }
