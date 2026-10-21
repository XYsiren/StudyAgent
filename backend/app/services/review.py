"""Explainable Leitner scheduling with explicit user grades."""

from datetime import timedelta

from app.models import utcnow

INTERVALS = (1, 2, 4, 7, 14, 30)


def schedule(card, grade):
    if grade == "forgot":
        card.level = 1
        card.failure_count += 1
    elif grade == "fuzzy":
        card.level = max(1, card.level - 1)
    else:
        card.level = min(6, card.level + (2 if grade == "easy" else 1))
    card.review_count += 1
    card.last_reviewed_at = utcnow()
    card.due_at = card.last_reviewed_at + timedelta(days=INTERVALS[card.level - 1])
