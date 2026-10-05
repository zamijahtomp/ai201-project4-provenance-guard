"""Combine the two signals into a verdict and confidence (see planning.md, Uncertainty representation)."""

LLM_WEIGHT = 0.6
STYLE_WEIGHT = 0.4
AI_THRESHOLD = 0.80      # ai_score at or above this (with agreement) => likely_ai
HUMAN_THRESHOLD = 0.40   # ai_score at or below this => likely_human
MIN_AGREEMENT = 0.65     # signals must be this close to each other to accuse
SHORT_TEXT_WORDS = 50    # below this, never issue likely_ai


def score_content(llm, style, word_count):
    """llm: {"score": ...} or None. style: {"score": ...}.

    Returns {ai_score, agreement, verdict, confidence}.
    """
    if llm is None:
        # Signal 1 unavailable: never a confident verdict from one signal.
        ai_score = style["score"]
        return {
            "ai_score": round(ai_score, 3),
            "agreement": None,
            "verdict": "uncertain",
            "confidence": round(1 - 2 * abs(ai_score - 0.5), 3),
        }

    ai_score = LLM_WEIGHT * llm["score"] + STYLE_WEIGHT * style["score"]
    agreement = 1 - abs(llm["score"] - style["score"])

    if ai_score >= AI_THRESHOLD and agreement >= MIN_AGREEMENT and word_count >= SHORT_TEXT_WORDS:
        verdict, confidence = "likely_ai", ai_score
    elif ai_score <= HUMAN_THRESHOLD:
        verdict, confidence = "likely_human", 1 - ai_score
    else:
        verdict, confidence = "uncertain", 1 - 2 * abs(ai_score - 0.5)

    return {
        "ai_score": round(ai_score, 3),
        "agreement": round(agreement, 3),
        "verdict": verdict,
        "confidence": round(confidence, 3),
    }
