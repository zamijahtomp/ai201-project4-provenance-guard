"""Transparency label text (verbatim from planning.md, Transparency label variants)."""

LABELS = {
    "likely_ai": (
        "🤖 Likely AI-generated. Our automated check found strong signs that this was "
        "written with AI tools. This is an estimate, not a certainty. If you wrote this "
        "yourself, the creator can request a review."
    ),
    "likely_human": (
        "✍️ Likely human-written. Our automated check found no strong signs of AI "
        "generation. No tool can be fully certain, but this looks like original human work."
    ),
    "uncertain": (
        "❓ Origin unclear. Our automated check could not tell whether this was written "
        "by a person or with AI tools. No conclusion should be drawn about the creator."
    ),
}

UNDER_REVIEW = (
    "🔍 Under review. The creator has asked for this label to be reviewed. "
    "A person is taking a second look."
)

INCOMPLETE_NOTE = " Automated check was incomplete."


def make_label(verdict, status="classified", incomplete=False):
    """Pick the label for a verdict. An open appeal overrides the verdict label."""
    if status == "under_review":
        return UNDER_REVIEW
    label = LABELS[verdict]
    return label + INCOMPLETE_NOTE if incomplete else label
