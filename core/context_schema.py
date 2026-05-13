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

ALL_DDL = (
    SQL_DDL_WINNING_HOOKS,
    SQL_INDEX_WINNING_HOOKS,
    SQL_DDL_REFERRAL_MOTIONS,
    SQL_DDL_NEGATIVE_CONSTRAINTS,
    SQL_INDEX_NEGATIVE_CONSTRAINTS,
)
