import uuid

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import db
from labels import make_label
from scoring import score_content
from signals import llm_signal, stylometric_signal

MIN_WORDS = 25  # below this, stylometric statistics are meaningless

# See README for the reasoning behind these numbers.
SUBMIT_LIMIT = "5 per minute;50 per day"
APPEAL_LIMIT = "10 per hour"

app = Flask(__name__)
limiter = Limiter(get_remote_address, app=app, default_limits=[], storage_uri="memory://")
db.init_db()


@app.errorhandler(429)
def rate_limited(e):
    return jsonify({"error": "rate limit exceeded", "limit": str(e.description)}), 429


@app.post("/submit")
@limiter.limit(SUBMIT_LIMIT)
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
    label = make_label(result["verdict"], status, incomplete=llm is None)

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
            "label": label,
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


@app.post("/appeal")
@limiter.limit(APPEAL_LIMIT)
def appeal():
    body = request.get_json(silent=True) or {}
    content_id = body.get("content_id")
    reasoning = body.get("creator_reasoning")
    creator_id = body.get("creator_id")  # optional; checked against the original if given

    if not isinstance(content_id, str) or not content_id.strip():
        return jsonify({"error": "content_id is required"}), 400
    if not isinstance(reasoning, str) or not reasoning.strip():
        return jsonify({"error": "creator_reasoning is required"}), 400

    content = db.get_content(content_id)
    if content is None:
        return jsonify({"error": "content not found"}), 404
    if creator_id is not None and creator_id != content["creator_id"]:
        return jsonify({"error": "only the original creator can appeal"}), 403
    if content["status"] == "under_review":
        return jsonify({"error": "an appeal is already open for this content"}), 409

    original = db.get_decision(content_id) or {}
    appeal_id = str(uuid.uuid4())
    db.file_appeal(content_id, content["creator_id"], reasoning.strip(), appeal_id)
    db.log_event(
        content_id,
        "appeal",
        {
            "appeal_id": appeal_id,
            "creator_id": content["creator_id"],
            "appeal_reasoning": reasoning.strip(),
            "status": "under_review",
            "original_attribution": original.get("attribution"),
            "original_confidence": original.get("confidence"),
            "llm_score": original.get("llm_score"),
            "stylometric_score": original.get("stylometric_score"),
        },
    )

    return jsonify({"content_id": content_id, "appeal_id": appeal_id, "status": "under_review"}), 200


@app.get("/content/<content_id>")
def get_content(content_id):
    content = db.get_content(content_id)
    if content is None:
        return jsonify({"error": "content not found"}), 404
    decision = db.get_decision(content_id) or {}
    return jsonify(
        {
            "content_id": content_id,
            "creator_id": content["creator_id"],
            "status": content["status"],
            "label": make_label(decision.get("attribution", "uncertain"), content["status"]),
            "original_decision": decision,
            "appeal": db.get_appeal(content_id),
        }
    )


@app.get("/log")
def log():
    return jsonify({"entries": db.get_log()})


if __name__ == "__main__":
    app.run(debug=True)
