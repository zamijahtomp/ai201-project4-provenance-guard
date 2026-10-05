import uuid

from flask import Flask, jsonify, request

import db
from scoring import score_content
from signals import llm_signal, stylometric_signal

MIN_WORDS = 25  # below this, stylometric statistics are meaningless

app = Flask(__name__)
db.init_db()


@app.post("/submit")
def submit():
    body = request.get_json(silent=True) or {}
    creator_id = body.get("creator_id")
    text = body.get("text")
    if not isinstance(creator_id, str) or not creator_id.strip():
        return jsonify({"error": "creator_id is required"}), 400
    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "text is required"}), 400
    word_count = len(text.split())
    if word_count < MIN_WORDS:
        return jsonify({"error": f"text must be at least {MIN_WORDS} words"}), 400

    content_id = str(uuid.uuid4())
    llm = llm_signal(text)  # None if Groq is unavailable
    style = stylometric_signal(text)
    result = score_content(llm, style, word_count)

    status = "classified"
    label = "[placeholder label - generated in Milestone 5]"

    db.save_content(content_id, creator_id, text, status)
    db.log_event(
        content_id,
        "decision",
        {
            "creator_id": creator_id,
            "attribution": result["verdict"],
            "confidence": result["confidence"],
            "ai_score": result["ai_score"],
            "agreement": result["agreement"],
            "llm_score": llm["score"] if llm else None,
            "stylometric_score": style["score"],
            "signals_used": ["llm", "stylometric"] if llm else ["stylometric"],
            "status": status,
        },
    )

    return (
        jsonify(
            {
                "content_id": content_id,
                "attribution": result["verdict"],
                "confidence": result["confidence"],
                "label": label,
                "scores": {"ai_score": result["ai_score"], "agreement": result["agreement"]},
                "signals": {"llm": llm, "stylometric": style},
                "status": status,
            }
        ),
        201,
    )


@app.get("/log")
def log():
    return jsonify({"entries": db.get_log()})


if __name__ == "__main__":
    app.run(debug=True)
