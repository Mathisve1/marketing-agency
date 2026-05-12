"""Pydantic models for MASTER_CONTEXT.md frontmatter.

The frontmatter is the canonical machine-readable surface shared across agents.
The markdown body below it is free-form prose for LLM context only.
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
    id: str = ""                       # blank on creation -> auto-assigned by ClientContext
    pattern: str
    description: str
    source_ad_id: Optional[str] = None
    days_active: Optional[int] = None  # longevity proxy
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
    """Top-level schema for MASTER_CONTEXT.md frontmatter."""
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    client: ClientIdentity
    brand: Brand = Field(default_factory=Brand)
    winning_hooks: list[WinningHook] = Field(default_factory=list)
    referral_motions: list[ReferralMotion] = Field(default_factory=list)
    negative_constraints: list[NegativeConstraint] = Field(default_factory=list)
    performance_benchmarks: PerformanceBenchmarks = Field(default_factory=PerformanceBenchmarks)
    asset_inventory: AssetInventory = Field(default_factory=AssetInventory)
