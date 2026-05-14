"""Pre-flight environment check for the marketing-agency repo.

What this script does (in order):

  1. Locate the repo root (walks up looking for pyproject.toml).
  2. Verify the expected top-level folder structure.
  3. Check that a `.env` file exists (without reading or printing values).
  4. Check the NAMES of required env vars (presence only, not values).
  5. Parse clients/_template/MASTER_CONTEXT.md YAML frontmatter.
  6. Verify the asset directories the template references actually exist.
  7. Onboard a throw-away client into a tmp dir to confirm
     ClientContext.onboard() + SQLite init still work end-to-end.
  8. Sanity-check the LangGraph checkpoint DB config and import.

What this script does NOT do:

  - It never makes a real network call. It does not contact Anthropic,
    Tavily, Apify, Kling, or Meta. It does not check that an API key is
    valid; it only checks that the variable NAME is set.
  - It never prints secret values.
  - It never modifies the working repo (the onboard test runs entirely
    inside a tempfile.TemporaryDirectory()).

Usage:
    python scripts/doctor.py

Exit codes:
    0  - all hard checks passed (warnings may still appear)
    1  - at least one hard check failed
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Optional

# stdlib only above. Project-internal imports happen INSIDE check functions
# so a missing dependency surfaces as a clean [FAIL] line rather than a
# top-of-file ImportError that kills the whole script.


PASS = "[PASS]"
FAIL = "[FAIL]"
WARN = "[WARN]"
INFO = "[INFO]"


REQUIRED_TOP_LEVEL_DIRS = (
    "agents",
    "core",
    "services",
    "ui",
    "tests",
    "scripts",
    "clients",
    "clients/_template",
)

REQUIRED_TOP_LEVEL_FILES = (
    "pyproject.toml",
    ".env.example",
    "mcp_server.py",
    "clients/_template/MASTER_CONTEXT.md",
)

# Env var names the running agents read. Doctor only checks presence of
# the NAME in os.environ - never the value, never whether it's valid.
REQUIRED_ENV_VAR_NAMES = (
    "ANTHROPIC_API_KEY",
    "TAVILY_API_KEY",
    "APIFY_API_TOKEN",
    "KLING_API_KEY",
    "KLING_API_SECRET",
    "META_ACCESS_TOKEN",
    "META_AD_ACCOUNT_ID",
)

# V1.7: name -> short provider/usage label. Doctor prints this map so a
# new operator can see WHICH integration each missing key blocks. Names
# only - never values, never any external API call.
ENV_VAR_PROVIDER_HINTS = {
    "ANTHROPIC_API_KEY":  "anthropic   (every agent's LLM calls)",
    "TAVILY_API_KEY":     "tavily      (Strategist + Outreach competitor search)",
    "APIFY_API_TOKEN":    "apify       (Strategist + Outreach FB ad-library scrape)",
    "KLING_API_KEY":      "kling       (Producer paid video submission)",
    "KLING_API_SECRET":   "kling       (Producer paid video submission)",
    "META_ACCESS_TOKEN":  "meta-ads    (Analyst Insights pull)",
    "META_AD_ACCOUNT_ID": "meta-ads    (Analyst Insights account scoping)",
}

# Subfolders referenced from clients/_template/MASTER_CONTEXT.md's
# asset_inventory block. Kept in lockstep with the template frontmatter.
TEMPLATE_REFERENCE_SUBDIRS = (
    "references/characters",
    "references/products",
    "references/referral_videos",
)


# --------------------------------------------------------------------------- #
# Output bookkeeping
# --------------------------------------------------------------------------- #


class CheckRunner:
    """Tiny accumulator. Prints as it goes; remembers totals for the summary.

    `failures` drives the exit code. `warnings` are informational only.
    """

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def passed(self, msg: str) -> None:
        print(f"{PASS} {msg}")

    def failed(self, msg: str) -> None:
        print(f"{FAIL} {msg}")
        self.failures.append(msg)

    def warned(self, msg: str) -> None:
        print(f"{WARN} {msg}")
        self.warnings.append(msg)

    def info(self, msg: str) -> None:
        print(f"{INFO} {msg}")


# --------------------------------------------------------------------------- #
# Repo root discovery
# --------------------------------------------------------------------------- #


def find_repo_root(start: Path) -> Path:
    """Walk up from `start` looking for a directory that contains
    pyproject.toml. Raises RuntimeError if none is found.

    Kept stateless and side-effect-free so it's trivially testable.
    """
    for candidate in [start, *start.parents]:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError(
        f"Could not find pyproject.toml walking up from {start}. "
        f"Are you running doctor.py from inside the repo?"
    )


# --------------------------------------------------------------------------- #
# Individual checks
# --------------------------------------------------------------------------- #


def check_repo_root(runner: CheckRunner) -> Optional[Path]:
    """Returns the repo root on success, None on failure (after logging)."""
    here = Path(__file__).resolve().parent
    try:
        root = find_repo_root(here)
    except RuntimeError as e:
        runner.failed(str(e))
        return None
    runner.passed(f"Repo root located at {root}")
    return root


def check_folder_structure(runner: CheckRunner, root: Path) -> None:
    for rel in REQUIRED_TOP_LEVEL_DIRS:
        path = root / rel
        if path.is_dir():
            runner.passed(f"Directory present: {rel}/")
        else:
            runner.failed(f"Missing directory: {rel}/")

    for rel in REQUIRED_TOP_LEVEL_FILES:
        path = root / rel
        if path.is_file():
            runner.passed(f"File present: {rel}")
        else:
            runner.failed(f"Missing file: {rel}")


def check_env_file(runner: CheckRunner, root: Path) -> None:
    """Confirm .env exists. Do NOT open it - we only care about presence."""
    env_path = root / ".env"
    if env_path.is_file():
        runner.passed(".env file present (contents not inspected)")
    else:
        runner.warned(
            ".env file missing at repo root. Copy .env.example to .env "
            "and fill in API keys before running the agents."
        )


def check_env_vars(runner: CheckRunner) -> None:
    """Check that required env var NAMES are set in the current process.

    We don't read or print values; we only check presence + non-emptiness.
    Streamlit / MCP load .env via python-dotenv at startup, so a developer
    running this from a shell that has not sourced .env will see [WARN]
    lines - that is informational, not a hard failure.
    """
    missing = [name for name in REQUIRED_ENV_VAR_NAMES if not os.environ.get(name)]
    if not missing:
        runner.passed(
            f"All {len(REQUIRED_ENV_VAR_NAMES)} required env vars are set "
            f"in this shell (values not inspected)"
        )
        return

    # V1.7: also show which provider each missing var blocks, so a new
    # operator doesn't have to grep the codebase to figure out "what
    # breaks if I leave TAVILY_API_KEY unset for now".
    lines = [
        "Some required env vars are not set in this shell"
        " (this is fine if Streamlit/MCP load them from .env at startup):",
    ]
    for name in missing:
        hint = ENV_VAR_PROVIDER_HINTS.get(name, "(no hint registered)")
        lines.append(f"        {name:<22}-> {hint}")
    runner.warned("\n".join(lines))


def check_master_context_template(runner: CheckRunner, root: Path) -> None:
    """Parse the YAML frontmatter of clients/_template/MASTER_CONTEXT.md.

    A broken template silently breaks every onboarding run, so we surface
    it loudly here.
    """
    try:
        import yaml  # noqa: PLC0415  (intentional: project-scoped import)
    except ImportError as e:
        runner.failed(f"PyYAML not installed: {e}")
        return

    template = root / "clients" / "_template" / "MASTER_CONTEXT.md"
    if not template.is_file():
        runner.failed(f"Missing template MASTER_CONTEXT.md at {template}")
        return

    text = template.read_text(encoding="utf-8")
    if not text.startswith("---"):
        runner.failed("Template MASTER_CONTEXT.md does not start with '---' frontmatter")
        return

    # Strip first '---' + find the closing '---'
    rest = text[len("---\n"):]
    end = rest.find("\n---\n")
    if end == -1:
        runner.failed("Template MASTER_CONTEXT.md frontmatter is not closed with '---'")
        return

    yaml_block = rest[:end]
    try:
        data = yaml.safe_load(yaml_block)
    except yaml.YAMLError as e:
        runner.failed(f"Template MASTER_CONTEXT.md YAML failed to parse: {e}")
        return

    if not isinstance(data, dict):
        runner.failed("Template MASTER_CONTEXT.md frontmatter is not a YAML mapping")
        return

    runner.passed("Template MASTER_CONTEXT.md parses as YAML")

    # Validate the asset_inventory directories the template advertises.
    inventory = data.get("asset_inventory") or {}
    for key in ("products_dir", "characters_dir", "referral_videos_dir"):
        rel = inventory.get(key)
        if not rel:
            runner.warned(f"asset_inventory.{key} not set in template")
            continue
        target = (root / "clients" / "_template" / rel).resolve()
        if target.is_dir():
            runner.passed(f"Template asset dir exists: {rel}")
        else:
            runner.warned(
                f"Template asset dir missing: {rel} "
                f"(run `python scripts/bootstrap_env.py` to create it)"
            )


def check_client_onboarding(runner: CheckRunner, root: Path) -> None:
    """Onboard a throw-away client inside a tempdir.

    Catches: broken imports, broken template copy, broken SQLite DDL,
    missing/renamed subdirs. Always runs against a copied template - the
    real clients/ directory is never touched.
    """
    try:
        from core.client_context import ClientContext  # noqa: PLC0415
    except ImportError as e:
        runner.failed(f"core.client_context failed to import: {e}")
        return

    template_src = root / "clients" / "_template"
    if not template_src.is_dir():
        runner.failed(f"Cannot run onboarding probe: template missing at {template_src}")
        return

    # ignore_cleanup_errors: on Windows, SQLite WAL sidecar files
    # (client_data.db-wal / .db-shm) can briefly retain a file lock after
    # the last connection closes - long enough that an immediate rmtree on
    # the enclosing tmp dir trips PermissionError. The probe's success is
    # established BEFORE cleanup runs; a failed teardown is OS bookkeeping
    # noise, not a doctor failure.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        clients_root = Path(tmp) / "clients"
        try:
            shutil.copytree(template_src, clients_root / "_template")
        except OSError as e:
            runner.failed(f"Could not copy template into tmp dir: {e}")
            return

        try:
            ctx = ClientContext.onboard(
                "doctorprobe", "Doctor Probe", "en-US", clients_root=clients_root
            )
        except Exception as e:
            runner.failed(
                f"ClientContext.onboard() raised "
                f"{type(e).__name__}: {e}"
            )
            return

        # Required runtime subdirs created?
        for rel in (
            "references/characters",
            "references/products",
            "references/referral_videos",
            "outputs/videos",
            "outputs/reports",
        ):
            if not (ctx.root / rel).is_dir():
                runner.failed(f"Onboard did not create {rel}/ under the silo")
                return

        # SQLite DB initialised with the V1.2/V1.4 schema?
        if not ctx.db_path.is_file():
            runner.failed(f"client_data.db was not created at {ctx.db_path}")
            return

        # Explicit close: stdlib sqlite3's `with` only commits/rolls back,
        # it does NOT close the connection. Leaving it dangling would
        # extend the Windows file-lock window past tmp cleanup.
        conn = sqlite3.connect(str(ctx.db_path))
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' ORDER BY name"
            ).fetchall()
        except sqlite3.Error as e:
            runner.failed(f"client_data.db unreadable: {e}")
            return
        finally:
            conn.close()

        table_names = {r[0] for r in rows}
        # We don't assert the FULL set (schema may grow); we just want the
        # core V1.2/V1.4 tables present.
        expected_tables = {
            "winning_hooks",
            "referral_motions",
            "negative_constraints",
            "video_plans",
            "video_jobs",
        }
        missing_tables = expected_tables - table_names
        if missing_tables:
            runner.failed(
                "client_data.db is missing tables: " + ", ".join(sorted(missing_tables))
            )
            return

    runner.passed(
        "Client onboarding probe succeeded (template copy + SQL init + cleanup)"
    )


def check_checkpoint_db_config(runner: CheckRunner) -> None:
    """Confirm the SqliteSaver checkpoint dependency is importable and the
    DEFAULT_CHECKPOINT_DB_PATH is sane.

    No DB file is created. We only verify:
      - langgraph.checkpoint.sqlite.SqliteSaver imports
      - core.supervisor exports DEFAULT_CHECKPOINT_DB_PATH
      - the resolved path is writable (parent exists or can be created)
    """
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: F401, PLC0415
    except ImportError as e:
        runner.failed(
            f"langgraph.checkpoint.sqlite.SqliteSaver not importable "
            f"({e}). Reinstall with `pip install -e .[dev]`."
        )
        return

    try:
        from core.supervisor import DEFAULT_CHECKPOINT_DB_PATH  # noqa: PLC0415
    except ImportError as e:
        runner.failed(f"core.supervisor failed to import: {e}")
        return

    path = Path(DEFAULT_CHECKPOINT_DB_PATH)
    parent = path.parent if str(path.parent) not in ("", ".") else Path.cwd()
    if not parent.exists():
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            runner.failed(
                f"Checkpoint DB parent dir not creatable at {parent}: {e}"
            )
            return

    runner.passed(
        f"LangGraph checkpoint config OK (path={path}, parent writable)"
    )


def check_operator_task_store(runner: CheckRunner) -> None:
    """PR A: confirm services.operator_task_store imports cleanly and its
    DEFAULT_DB_PATH parent is writable. Schema smoke test against a tmp
    DB so a broken DDL surfaces here, not the first time Streamlit /
    Manager / daily_summary tries to sync.

    Never touches the production operator_tasks.db at repo root, never
    calls a network. Pure schema-init test in a tmp dir.
    """
    try:
        from services import operator_task_store  # noqa: PLC0415
    except ImportError as e:
        runner.failed(f"services.operator_task_store not importable: {e}")
        return

    prod_path = Path(operator_task_store.DEFAULT_DB_PATH)
    parent = prod_path.parent if str(prod_path.parent) not in ("", ".") else Path.cwd()
    if not parent.exists():
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            runner.failed(
                f"Operator task store parent dir not creatable at {parent}: {e}"
            )
            return

    import tempfile  # noqa: PLC0415
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp_db = Path(tmp) / "operator_tasks.db"
        try:
            # list_open_tasks fires _init_schema as a side effect; if any
            # DDL is malformed, this raises here rather than in the UI.
            assert operator_task_store.list_open_tasks(db_path=tmp_db) == []
            # Also exercise the sync entry point with an empty inferred
            # list - catches a typo in the auto-close SQL that doesn't
            # show up on the read-only path.
            operator_task_store.sync_inferred_tasks([], db_path=tmp_db)
        except Exception as e:
            runner.failed(f"Operator task store schema init failed: {e}")
            return

    runner.passed(
        f"Operator task store OK (path={prod_path}, parent writable, "
        f"schema initialises)"
    )


def check_cost_ledger(runner: CheckRunner) -> None:
    """PR B / Pass 2.2: confirm services.cost_ledger imports cleanly,
    its DEFAULT_DB_PATH parent is writable, and the schema initialises
    against a tmp DB. Also smoke-tests record_event's safety contract:
    calling it must never raise, even when the DB is in a clean state.

    Never touches the production cost_ledger.db at repo root, never
    calls a network. Pure schema-init test in a tmp dir.
    """
    try:
        from services import cost_ledger as _cl  # noqa: PLC0415
    except ImportError as e:
        runner.failed(f"services.cost_ledger not importable: {e}")
        return

    prod_path = Path(_cl.DEFAULT_DB_PATH)
    parent = prod_path.parent if str(prod_path.parent) not in ("", ".") else Path.cwd()
    if not parent.exists():
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            runner.failed(
                f"Cost ledger parent dir not creatable at {parent}: {e}"
            )
            return

    import tempfile  # noqa: PLC0415
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp_db = Path(tmp) / "cost_ledger.db"
        try:
            # list_recent fires _init_schema as a side effect; if any DDL
            # or index DDL is malformed, this raises here rather than
            # the first time an agent tries to record a paid call.
            assert _cl.list_recent(limit=1, db_path=tmp_db) == []
            # Exercise the safety contract: record_event must NEVER raise.
            # We pass valid input (so the row should appear), then verify
            # it round-trips. A failure to record would be silent (per
            # design), but list_recent would also be empty.
            _cl.record_event(
                provider="doctor",
                event_type="probe",
                units=1.0,
                estimated_cost_eur=0.0,
                metadata={"probe": True},
                db_path=tmp_db,
            )
            probed = _cl.list_recent(limit=5, db_path=tmp_db)
            assert len(probed) == 1, (
                f"cost ledger probe did not persist (got {len(probed)} rows)"
            )
        except Exception as e:
            runner.failed(f"Cost ledger schema/probe failed: {e}")
            return

    runner.passed(
        f"Cost ledger OK (path={prod_path}, parent writable, "
        f"schema initialises, record_event probe round-trips)"
    )


def check_daily_summary_imports(runner: CheckRunner) -> None:
    """PR A: confirm scripts/daily_summary.py loads and exposes a `main`
    callable. The script is not a package member (scripts/ has no
    __init__.py), so we load it via importlib.util like tests/test_doctor.py
    does for doctor.py itself. No DB call, no I/O, no Task Scheduler
    trigger - purely an import sanity check.
    """
    import importlib.util  # noqa: PLC0415

    here = Path(__file__).resolve().parent
    script_path = here / "daily_summary.py"
    if not script_path.is_file():
        runner.failed(f"scripts/daily_summary.py missing at {script_path}")
        return

    try:
        spec = importlib.util.spec_from_file_location(
            "daily_summary_doctor_probe", script_path,
        )
        if spec is None or spec.loader is None:
            runner.failed(
                f"scripts/daily_summary.py spec could not be built from {script_path}"
            )
            return
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as e:
        runner.failed(
            f"scripts/daily_summary.py failed to import: "
            f"{type(e).__name__}: {e}"
        )
        return

    main_fn = getattr(module, "main", None)
    if not callable(main_fn):
        runner.failed("scripts/daily_summary.py does not expose a callable main()")
        return

    render_fn = getattr(module, "render_summary", None)
    if not callable(render_fn):
        runner.failed("scripts/daily_summary.py does not expose render_summary()")
        return

    runner.passed("Daily summary script OK (loads cleanly, exposes main + render_summary)")


def check_mcp_pending_registry(runner: CheckRunner) -> None:
    """V1.7: confirm services.mcp_pending_store imports cleanly and its
    DEFAULT_DB_PATH is in a writable location. Also probes the schema by
    calling list_pending() against a tmp DB - if the DDL is broken the
    error surfaces here, not the first time MCP tries to record a
    pending run.

    Never touches the production mcp_pending_runs.db at repo root, never
    calls a network. Pure schema-init smoke test in tmp.
    """
    try:
        from services import mcp_pending_store  # noqa: PLC0415
    except ImportError as e:
        runner.failed(f"services.mcp_pending_store not importable: {e}")
        return

    prod_path = Path(mcp_pending_store.DEFAULT_DB_PATH)
    parent = prod_path.parent if str(prod_path.parent) not in ("", ".") else Path.cwd()
    if not parent.exists():
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            runner.failed(f"MCP pending registry parent dir not creatable at {parent}: {e}")
            return

    # Schema smoke test against a tmp DB - never touches the real one.
    # ignore_cleanup_errors=True because Windows can hold the SQLite
    # WAL/SHM sidecars briefly after the connection closes; the OS will
    # garbage-collect the tmp dir later.
    import tempfile  # noqa: PLC0415
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp_db = Path(tmp) / "mcp_pending_runs.db"
        try:
            assert mcp_pending_store.list_pending(db_path=tmp_db) == []
        except Exception as e:
            runner.failed(f"MCP pending registry schema init failed: {e}")
            return

    runner.passed(
        f"MCP pending registry OK (path={prod_path}, parent writable, schema initialises)"
    )


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #


def main() -> int:
    print("marketing-agency: doctor.py")
    print("=" * 60)
    runner = CheckRunner()

    root = check_repo_root(runner)
    if root is None:
        print("=" * 60)
        print(f"Result: {len(runner.failures)} failure(s); cannot continue.")
        return 1

    check_folder_structure(runner, root)
    check_env_file(runner, root)
    check_env_vars(runner)
    check_master_context_template(runner, root)
    check_client_onboarding(runner, root)
    check_checkpoint_db_config(runner)
    check_mcp_pending_registry(runner)
    check_operator_task_store(runner)
    check_daily_summary_imports(runner)
    check_cost_ledger(runner)

    print("=" * 60)
    print(
        f"Result: {len(runner.failures)} failure(s), "
        f"{len(runner.warnings)} warning(s)."
    )
    return 1 if runner.failures else 0


if __name__ == "__main__":
    sys.exit(main())
