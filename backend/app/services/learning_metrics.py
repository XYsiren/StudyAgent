from datetime import UTC, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models import Document
from app.models_learning import KnowledgePoint, LearningEvent, QuizAttempt, QuizItem, StudyPlan, StudyTask
from app.services.learning import today, validate_sources


def metrics(db, user, course):
    points = db.scalars(
        select(KnowledgePoint).where(KnowledgePoint.course_id == course.id, KnowledgePoint.confirmed_at.is_not(None))
    ).all()
    quizzes = db.scalars(select(QuizItem).where(QuizItem.course_id == course.id)).all()
    # Invalidated source revisions and expired documents do not contribute learning claims.
    quizzes = [q for q in quizzes if validate_sources(db, user, course, q.citations)]
    ids = [q.id for q in quizzes]
    attempts = db.scalars(
        select(QuizAttempt).where(QuizAttempt.user_id == user.id, QuizAttempt.quiz_item_id.in_(ids))
    ).all()
    tasks = db.scalars(select(StudyTask).join(StudyPlan).where(StudyPlan.course_id == course.id)).all()
    docs = db.scalars(select(Document).where(Document.knowledge_base_id == course.knowledge_base_id)).all()
    graded = [a for a in attempts if a.is_correct is not None]
    included = [t for t in tasks if t.status != "skipped"]
    events = db.scalars(select(LearningEvent).where(LearningEvent.course_id == course.id)).all()
    active_dates = {
        e.created_at.replace(tzinfo=UTC).astimezone(ZoneInfo("Asia/Shanghai")).date()
        for e in events
        if e.event_name
        in {"quiz_attempt_submitted", "review_card_graded", "study_task_completed", "tutor_answer_cited"}
    }
    streak, day = 0, today()
    if day not in active_dates:
        day -= timedelta(days=1)
    while day in active_dates:
        streak += 1
        day -= timedelta(days=1)
    previous_wrong, repeated, repeated_wrong = set(), set(), set()
    for a in sorted(graded, key=lambda a: a.created_at):
        if a.quiz_item_id in previous_wrong:
            repeated.add(a.quiz_item_id)
            if not a.is_correct:
                repeated_wrong.add(a.quiz_item_id)
        if not a.is_correct:
            previous_wrong.add(a.quiz_item_id)
    period = {
        "from": course.created_at.replace(tzinfo=UTC).astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat(),
        "to": today().isoformat(),
        "scope": "当前课程、当前有效资料版本",
    }

    def metric(value, size, definition):
        return {
            "value": value,
            "sample_size": size,
            "period": period,
            "definition_version": "learning-v1",
            "definition": definition,
            "insufficient_sample": size < 5,
        }

    tutor = [e for e in events if e.event_name == "tutor_answer_cited"]
    return {
        "materials": metric(len(docs), len(docs), "课程资料数量"),
        "knowledge_points": metric(len(points), len(points), "已确认知识点数量"),
        "mastery": metric(
            sum(p.mastery_state == "mastered" for p in points) / len(points) if points else None,
            len(points),
            "已掌握/已确认知识点",
        ),
        "quiz_count": metric(len(quizzes), len(quizzes), "来源仍有效的练习题数量"),
        "attempt_count": metric(len(attempts), len(attempts), "当前有效题目的作答数量"),
        "accuracy": metric(
            sum(a.is_correct is True for a in graded) / len(graded) if graded else None,
            len(graded),
            "正确作答/已判定作答；待人工评分不计入",
        ),
        "repeat_error_rate": metric(
            len(repeated_wrong) / len(repeated) if repeated else None,
            len(repeated),
            "再次答错的原错题数/进入再次练习的错题数（累计）",
        ),
        "plan_completion": metric(
            sum(t.status == "completed" for t in included) / len(included) if included else None,
            len(included),
            "完成任务/未跳过任务",
        ),
        "streak_days": metric(streak, len(active_dates), "以北京时间统计的连续学习天数"),
        "citation_coverage": metric(
            sum(bool(e.properties.get("cited")) for e in tutor) / len(tutor) if tutor else None,
            len(tutor),
            "有引用的回答/课程问答响应；非逐结论人工核验",
        ),
        "unsupported_blocked": metric(
            sum(bool(e.properties.get("abstained")) for e in tutor), len(tutor), "课程问答中无依据拒答次数"
        ),
    }
