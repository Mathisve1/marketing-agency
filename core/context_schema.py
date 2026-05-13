"""Pydantic models for MASTER_CONTEXT.md frontmatter + SQL DDL for the
client's local SQLite database.

V1.2 refactor: the three dynamic lists (winning_hooks, referral_motions,
negative_constraints) moved from YAML frontmatter into a per-client
SQLite database (clients/<id>/client_data.db). MASTER_CONTEXT.md now
holds only static metadata: client identity, brand voice / forbidden
terms / primary products, performance benchmarks, asset inventory paths.

The Pydantic models for the dynamic items (WinningHook, ReferralMotion,
NegativeConstraint) are kept verbatim - they're still the canonical
in-memory representation, used as tool args / return types / SQL row
adapters. Only their *storage location* changed.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class Severity(str, Enum):
    HARD = "hard"
    SOFT = "soft"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AddedBy(str, Enum):
    STRATEGIST = "strategist"
    ANALYST = "analyst"
    PRODUCER = "producer"
    HUMAN = "human"


class ClientIdentity(BaseModel):
    id: str
    name: str
    locale: str = "nl-BE"
    last_updated: datetime


class Brand(BaseModel):
    voice_attributes: list[str] = Field(default_factory=list)
    forbidden_terms: list[str] = Field(default_factory=list)
    primary_products: list[str] = Field(default_factory=list)
    brand_safety_notes: Optional[str] = None


class WinningHook(BaseModel):
    id: str = ""                       # blank on creation -> auto-assigned via SQL
    pattern: str
    description: str
    source_ad_id: Optional[str] = None
    days_active: Optional[int] = None
    confidence: Confidence = Confidence.MEDIUM
    added_by: AddedBy
    added_at: datetime


class ReferralMotion(BaseModel):
    id: str = ""
    description: str
    reference_path: str                # relative to client root
    pacing: Optional[str] = None
    camera_style: Optional[str] = None
    duration_seconds: Optional[int] = None
    added_by: AddedBy
    added_at: datetime


class NegativeConstraint(BaseModel):
    id: str = ""
    rule: str
    reason: str
    severity: Severity = Severity.SOFT
    added_by: AddedBy
    added_at: datetime
    source_log_entries: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Producer plan + job records (V1.4 - replaces performance_log.json)
# --------------------------------------------------------------------------- #


class PlanStatus(str, Enum):
    PENDING_APPROVAL = "pending_approval"
    SUBMITTING = "submitting"          # V1.4.1: atomic claim has been made;
                                        # only the claiming process may call Kling.
    REJECTED = "rejected"
    SUBMITTED = "submitted"


class JobStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class VideoPlan(BaseModel):
    """A compiled, deterministic Kling brief awaiting human approval.

    Created by the Producer planning node's compile_video_plan tool. Holds
    the full Kling submission envelope so the operator can review the EXACT
    prompt, negative_prompt, asset paths, duration, and aspect_ratio before
    any paid call. Once approved, the producer_submit node reads this row,
    submits to Kling, and writes a corresponding video_jobs row.
    """
    id: str = ""                          # blank on creation -> auto-assigned (VP-NNN)
    status: PlanStatus = PlanStatus.PENDING_APPROVAL
    hook_id: str
    motion_id: Optional[str] = None
    character_asset: str                  # relative to client root
    product_asset: str
    duration: int
    aspect_ratio: str
    mode: str
    cfg_scale: float
    prompt: str                           # full Kling prompt as compiled
    negative_prompt: str
    enforced_constraint_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    decided_at: Optional[datetime] = None
    decided_by: Optional[str] = None      # 'human' | 'auto' | None
    # V1.4.1 audit fields for the atomic claim flow.
    # submit_attempts increments every time claim_plan_for_submission succeeds.
    # submit_attempted_at is the timestamp of the LAST claim.
    # submit_error is set by release_plan_after_submit_failure; cleared on
    # successful mark_plan_submitted. If non-None on a pending_approval plan,
    # the plan was reverted from a failed submission attempt and the operator
    # should review the error before re-approving.
    submit_attempts: int = 0
    submit_attempted_at: Optional[datetime] = None
    submit_error: Optional[str] = None


class VideoJob(BaseModel):
    """A submitted Kling task. Mutable state only; everything else lives on
    the parent video_plans row reachable via plan_id."""
    kling_task_id: str
    plan_id: str
    status: JobStatus = JobStatus.PENDING
    video_path: Optional[str] = None      # relative to client root; set on completion
    error: Optional[str] = None
    submitted_at: datetime
    completed_at: Optional[datetime] = None


class PerformanceBenchmarks(BaseModel):
    roas_target: Optional[float] = None
    ctr_target: Optional[float] = None
    last_analyst_run: Optional[datetime] = None


class AssetInventory(BaseModel):
    products_dir: str = "references/products/"
    characters_dir: str = "references/characters/"
    referral_videos_dir: str = "references/referral_videos/"


class MasterContext(BaseModel):
    """V1.2 frontmatter: STATIC client metadata only.

    Dynamic lists (winning_hooks, referral_motions, negative_constraints)
    moved to clients/<id>/client_data.db. Use ClientContext.get_winning_hooks
    / get_referral_motions / get_negative_constraints to fetch them.

    extra='ignore' rather than 'forbid' so pre-V1.2 YAML files (which still
    carry the dropped list keys) load without error during the auto-migration.
    """
    model_config = ConfigDict(extra="ignore")

    schema_version: int = 2
    client: ClientIdentity
    brand: Brand = Field(default_factory=Brand)
    performance_benchmarks: PerformanceBenchmarks = Field(default_factory=PerformanceBenchmarks)
    asset_inventory: AssetInventory = Field(default_factory=AssetInventory)


# --------------------------------------------------------------------------- #
# SQL DDL - executed by ClientContext._init_db() on first load.
# CHECK constraints mirror the Pydantic enum value-sets so the DB rejects
# invalid rows even if a future caller bypasses Pydantic.
# --------------------------------------------------------------------------- #

SQL_DDL_WINNING_HOOKS = """
CREATE TABLE IF NOT EXISTS winning_hooks (
    id            TEXT PRIMARY KEY,
    pattern       TEXT NOT NULL,
    description   TEXT NOT NULL,
    source_ad_id  TEXT,
    days_active   INTEGER,
    confidence    TEXT NOT NULL CHECK(confidence IN ('high', 'medium', 'low')),
    added_by      TEXT NOT NULL CHECK(added_by IN ('strategist', 'analyst', 'producer', 'human')),
    added_at      TEXT NOT NULL
)
"""

SQL_INDEX_WINNING_HOOKS = """
CREATE INDEX IF NOT EXISTS idx_winning_hooks_confidence_added_at
    ON winning_hooks (confidence, added_at DESC)
"""

SQL_DDL_REFERRAL_MOTIONS = """
CREATE TABLE IF NOT EXISTS referral_motions (
    id                TEXT PRIMARY KEY,
    description       TEXT NOT NULL,
    reference_path    TEXT NOT NULL,
    pacing            TEXT,
    camera_style      TEXT,
    duration_seconds  INTEGER,
    added_by          TEXT NOT NULL CHECK(added_by IN ('strategist', 'analyst', 'producer', 'human')),
    added_at          TEXT NOT NULL
)
"""

SQL_DDL_NEGATIVE_CONSTRAINTS = """
CREATE TABLE IF NOT EXISTS negative_constraints (
    id                  TEXT PRIMARY KEY,
    rule                TEXT NOT NULL,
    reason              TEXT NOT NULL,
    severity            TEXT NOT NULL CHECK(severity IN ('hard', 'soft')),
    added_by            TEXT NOT NULL CHECK(added_by IN ('strategist', 'analyst', 'producer', 'human')),
    added_at            TEXT NOT NULL,
    source_log_entries  TEXT NOT NULL DEFAULT '[]'
)
"""

SQL_INDEX_NEGATIVE_CONSTRAINTS = """
CREATE INDEX IF NOT EXISTS idx_negative_constraints_severity
    ON negative_constraints (severity)
"""

# --------------------------------------------------------------------------- #
# V1.4 DDL: video plans and jobs
# --------------------------------------------------------------------------- #

SQL_DDL_VIDEO_PLANS = """
CREATE TABLE IF NOT EXISTS video_plans (
    id                       TEXT PRIMARY KEY,
    status                   TEXT NOT NULL CHECK(status IN ('pending_approval', 'submitting', 'rejected', 'submitted')),
    hook_id                  TEXT NOT NULL,
    motion_id                TEXT,
    character_asset          TEXT NOT NULL,
    product_asset            TEXT NOT NULL,
    duration                 INTEGER NOT NULL,
    aspect_ratio             TEXT NOT NULL,
    mode                     TEXT NOT NULL,
    cfg_scale                REAL NOT NULL,
    prompt                   TEXT NOT NULL,
    negative_prompt          TEXT NOT NULL,
    enforced_constraint_ids  TEXT NOT NULL DEFAULT '[]',
    created_at               TEXT NOT NULL,
    decided_at               TEXT,
    decided_by               TEXT,
    submit_attempts          INTEGER NOT NULL DEFAULT 0,
    submit_attempted_at      TEXT,
    submit_error             TEXT
)
"""

SQL_INDEX_VIDEO_PLANS_STATUS = """
CREATE INDEX IF NOT EXISTS idx_video_plans_status_created
    ON video_plans (status, created_at DESC)
"""

SQL_DDL_VIDEO_JOBS = """
CREATE TABLE IF NOT EXISTS video_jobs (
    kling_task_id  TEXT PRIMARY KEY,
    plan_id        TEXT NOT NULL REFERENCES video_plans(id),
    status         TEXT NOT NULL CHECK(status IN ('pending', 'completed', 'failed')),
    video_path     TEXT,
    error          TEXT,
    submitted_at   TEXT NOT NULL,
    completed_at   TEXT
)
"""

SQL_INDEX_VIDEO_JOBS_STATUS = """
CREATE INDEX IF NOT EXISTS idx_video_jobs_status
    ON video_jobs (status, submitted_at DESC)
"""

ALL_DDL = (
    SQL_DDL_WINNING_HOOKS,
    SQL_INDEX_WINNING_HOOKS,
    SQL_DDL_REFERRAL_MOTIONS,
    SQL_DDL_NEGATIVE_CONSTRAINTS,
    SQL_INDEX_NEGATIVE_CONSTRAINTS,
    SQL_DDL_VIDEO_PLANS,
    SQL_INDEX_VIDEO_PLANS_STATUS,
    SQL_DDL_VIDEO_JOBS,
    SQL_INDEX_VIDEO_JOBS_STATUS,
)
