"""
Data retention & right-to-be-forgotten (Phase 4).

delete_account(user_id) performs a full erasure of a user's personal data:
  - purges financial_profile, preferences, saved_plans (and their owned
    recommendation_snapshots)
  - DE-IDENTIFIES audit_log rows (user_id -> NULL) rather than deleting them,
    so the compliance/reproducibility trail is retained without personal data
  - deletes the user_credentials row and the users row

The two-step soft-then-hard pattern from the data model is supported:
  soft_delete_account() sets users.deleted_at (marks for erasure); a scheduled
  purge then calls delete_account() to hard-erase. Calling delete_account()
  directly performs the hard erasure immediately (used by the DELETE endpoint).

Returns a summary of what was removed/de-identified for the audit of the
erasure itself.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def soft_delete_account(user_id: str) -> bool:
    """Mark a user for erasure (users.deleted_at = now). Returns True if marked."""
    import db
    from db.models import User

    if not db.is_enabled():
        raise RuntimeError("Database is not configured.")
    session = db.get_session()
    user = session.get(User, uuid.UUID(str(user_id)))
    if user is None:
        return False
    user.deleted_at = datetime.now(timezone.utc)
    session.commit()
    return True


def delete_account(user_id: str) -> dict:
    """
    Hard-erase a user's personal data and de-identify their audit trail.

    Idempotent-ish: if the user is already gone, returns zeros. Runs in one
    transaction; rolls back on failure.
    """
    import db
    from db.models import (
        AuditLog,
        FinancialProfile,
        Preferences,
        RecommendationSnapshot,
        SavedPlan,
        User,
        UserCredential,
    )

    if not db.is_enabled():
        raise RuntimeError("Database is not configured.")

    uid = uuid.UUID(str(user_id))
    session = db.get_session()
    summary = {"profiles": 0, "preferences": 0, "saved_plans": 0, "snapshots": 0,
               "credentials": 0, "audit_deidentified": 0, "user_deleted": False}

    try:
        # Saved plans + their owned recommendation snapshots.
        plans = session.query(SavedPlan).filter(SavedPlan.user_id == uid).all()
        snapshot_ids = [p.recommendation_snapshot_id for p in plans if p.recommendation_snapshot_id]
        for p in plans:
            session.delete(p)
        summary["saved_plans"] = len(plans)
        if snapshot_ids:
            snaps = (
                session.query(RecommendationSnapshot)
                .filter(RecommendationSnapshot.id.in_(snapshot_ids))
                .all()
            )
            for sn in snaps:
                session.delete(sn)
            summary["snapshots"] = len(snaps)

        # Profile + preferences + credential (1:1 rows).
        prof = session.get(FinancialProfile, uid)
        if prof is not None:
            session.delete(prof)
            summary["profiles"] = 1
        prefs = session.get(Preferences, uid)
        if prefs is not None:
            session.delete(prefs)
            summary["preferences"] = 1
        cred = session.get(UserCredential, uid)
        if cred is not None:
            session.delete(cred)
            summary["credentials"] = 1

        # De-identify audit rows (retain for compliance; drop the user link).
        deidentified = (
            session.query(AuditLog)
            .filter(AuditLog.user_id == uid)
            .update({AuditLog.user_id: None}, synchronize_session=False)
        )
        summary["audit_deidentified"] = int(deidentified or 0)

        # Finally remove the user row itself.
        user = session.get(User, uid)
        if user is not None:
            session.delete(user)
            summary["user_deleted"] = True

        session.commit()
        logger.info("Erased account %s: %s", user_id, summary)
        return summary
    except Exception:
        session.rollback()
        logger.exception("Account erasure failed for %s; rolled back.", user_id)
        raise
