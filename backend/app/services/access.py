"""One authorization policy for documents, retrieval and organization management."""
from fastapi import HTTPException
from sqlalchemy import or_, select

from app.models import AuditEvent, KnowledgeBase, KnowledgeBaseGrant, Membership


def allowed_kbs(user_id):
    memberships = select(Membership.organization_id).where(Membership.user_id == user_id)
    administrators = select(Membership.organization_id).where(Membership.user_id == user_id, Membership.role == "admin")
    grants = select(KnowledgeBaseGrant.knowledge_base_id).where(KnowledgeBaseGrant.user_id == user_id)
    return or_((KnowledgeBase.organization_id.is_(None)) & (KnowledgeBase.user_id == user_id),
               KnowledgeBase.organization_id.in_(memberships) & or_(KnowledgeBase.visibility == "organization",
                   KnowledgeBase.organization_id.in_(administrators), KnowledgeBase.id.in_(grants)))


def kb_role(db, user, kb):
    if not kb.organization_id:
        return "admin"
    member = db.get(Membership, (kb.organization_id, user.id))
    if not member:
        return None
    if member.role == "admin" or kb.visibility == "organization":
        return member.role
    grant = db.get(KnowledgeBaseGrant, (kb.id, user.id))
    return grant.role if grant else None


def require_kb(db, user, kb_id, action="read"):
    kb = db.scalar(select(KnowledgeBase).where(KnowledgeBase.id == kb_id, allowed_kbs(user.id)))
    if kb is None:
        raise HTTPException(404, "知识库不存在")
    role = kb_role(db, user, kb)
    if (action == "write" and role not in {"admin", "editor"}) or (action == "admin" and role != "admin"):
        raise HTTPException(403, "当前角色没有此操作权限")
    return kb


def require_org(db, user, org_id, admin=False):
    member = db.get(Membership, (org_id, user.id))
    if member is None:
        raise HTTPException(404, "组织不存在")
    if admin and member.role != "admin":
        raise HTTPException(403, "需要组织管理员权限")
    return member


def audit(db, user_id, action, resource_id, org_id=None, request_id=""):
    # Never record raw questions, source content, tokens or passwords in audit events.
    db.add(AuditEvent(actor_id=user_id, action=action, resource_id=resource_id,
                      organization_id=org_id, request_id=request_id))
