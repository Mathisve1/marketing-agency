"""Tests for the private microsite builder.

Covers:
  - private slug generation (random 6-char token, alphanumeric)
  - token persistence: existing manifest token is reused
  - manifest schema: public_url, noindex, status="draft", review_required
  - preview-video gating: only `preview_watermarked.mp4` is embedded;
    a bare `preview.mp4` is logged and ignored
  - deploy package layout
  - no-public-index discipline (hygiene fence)
"""
from __future__ import annotations

import json
from pathlib import Path

from agents.outreach.reporting.microsite_builder import (
    DEFAULT_PUBLIC_BASE_URL,
    TOKEN_ALPHABET,
    TOKEN_LENGTH,
    WATERMARKED_PREVIEW_FILENAME,
    build_deploy_package,
    build_microsite,
)
from tests.test_html_deck_builder import _make_real_png, _make_synthetic_brief

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _setup_prospect(tmp_path: Path, prospect_id: str = "acme-skincare"):
    """Create a synthetic prospect tree under `tmp_path/prospects/<id>/`."""
    prospect_root = tmp_path / "prospects" / prospect_id
    prospect_root.mkdir(parents=True, exist_ok=True)
    brief = _make_synthetic_brief(prospect_root)
    return prospect_root, brief


# --------------------------------------------------------------------------- #
# Private slug + token
# --------------------------------------------------------------------------- #


def test_private_slug_includes_random_token(tmp_path: Path):
    """`<brand-slug>-<6char-token>` shape; token is alphanumeric lowercase."""
    _, brief = _setup_prospect(tmp_path)
    manifest = build_microsite("acme-skincare", brief=brief)
    slug = manifest["private_slug"]
    assert slug.startswith("acme-skincare-"), slug
    token = slug.rsplit("-", 1)[-1]
    assert len(token) == TOKEN_LENGTH
    assert all(c in TOKEN_ALPHABET for c in token), token


def test_existing_manifest_token_is_reused(tmp_path: Path):
    """Second build keeps the slug that was generated on the first build."""
    _, brief = _setup_prospect(tmp_path)
    m1 = build_microsite("acme-skincare", brief=brief)
    m2 = build_microsite("acme-skincare", brief=brief)
    assert m1["private_slug"] == m2["private_slug"]
    assert m1["public_url"] == m2["public_url"]
    # created_at also sticky.
    assert m1["created_at"] == m2["created_at"]


def test_manifest_has_public_url(tmp_path: Path):
    _, brief = _setup_prospect(tmp_path)
    manifest = build_microsite("acme-skincare", brief=brief)
    url = manifest["public_url"]
    assert url.startswith(DEFAULT_PUBLIC_BASE_URL), url
    assert url.endswith("/"), "public URL should end with a slash"
    assert manifest["private_slug"] in url


def test_manifest_status_and_review_flags(tmp_path: Path):
    _, brief = _setup_prospect(tmp_path)
    manifest = build_microsite("acme-skincare", brief=brief)
    assert manifest["status"] == "draft"
    assert manifest["review_required"] is True


def test_manifest_persisted_on_disk_matches_returned_dict(tmp_path: Path):
    prospect_root, brief = _setup_prospect(tmp_path)
    manifest = build_microsite("acme-skincare", brief=brief)
    on_disk = json.loads(
        (prospect_root / "site" / "manifest.json").read_text(encoding="utf-8")
    )
    assert on_disk["private_slug"] == manifest["private_slug"]
    assert on_disk["public_url"] == manifest["public_url"]


# --------------------------------------------------------------------------- #
# Privacy: noindex meta tag
# --------------------------------------------------------------------------- #


def test_noindex_meta_tag_is_present(tmp_path: Path):
    prospect_root, brief = _setup_prospect(tmp_path)
    build_microsite("acme-skincare", brief=brief)
    html = (prospect_root / "site" / "index.html").read_text(encoding="utf-8")
    assert '<meta name="robots" content="noindex,nofollow">' in html


def test_deck_builder_default_does_not_set_noindex(tmp_path: Path):
    """The bare deck builder must NOT emit noindex unless asked - it is
    only the microsite path that needs the privacy gate."""
    from agents.outreach.reporting.html_deck_builder import build_html_deck
    _, brief = _setup_prospect(tmp_path)
    out = build_html_deck(brief)
    html = out.read_text(encoding="utf-8")
    assert "noindex,nofollow" not in html


# --------------------------------------------------------------------------- #
# Preview video gating
# --------------------------------------------------------------------------- #


def _write_fake_video(path: Path, size_bytes: int = 64_000) -> Path:
    """Minimal MP4-looking blob (header + padding). Microsite builder
    only checks for existence + extension; the deck embeds via a <source>
    tag so the byte content does not matter for assertions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # ftypisom marker so the file has the right magic bytes for completeness.
    path.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * (size_bytes - 12))
    return path


def test_preview_video_section_appears_when_watermarked_mp4_present(tmp_path: Path):
    prospect_root, brief = _setup_prospect(tmp_path)
    _write_fake_video(prospect_root / "assets" / WATERMARKED_PREVIEW_FILENAME)
    manifest = build_microsite("acme-skincare", brief=brief)
    html = (prospect_root / "site" / "index.html").read_text(encoding="utf-8")
    assert "Your first route preview" in html
    assert "Want the clean version?" in html
    assert "<video" in html
    assert manifest.get("preview_video_path") == f"assets/{WATERMARKED_PREVIEW_FILENAME}"
    assert manifest.get("watermarked_video") is True


def test_unwatermarked_preview_mp4_is_not_embedded(tmp_path: Path):
    """A bare `preview.mp4` must be ignored - only `_watermarked` files
    are embedded. The microsite still renders without a video slide."""
    prospect_root, brief = _setup_prospect(tmp_path)
    # ONLY the bare preview.mp4 (no watermarked variant).
    _write_fake_video(prospect_root / "assets" / "preview.mp4")
    manifest = build_microsite("acme-skincare", brief=brief)
    html = (prospect_root / "site" / "index.html").read_text(encoding="utf-8")
    assert "Your first route preview" not in html
    assert "<video" not in html
    assert "preview_video_path" not in manifest
    assert "watermarked_video" not in manifest


def test_preview_oversize_warning_in_manifest(tmp_path: Path):
    """Cloudflare Pages safe asset budget is 25 MB; oversize triggers a warning."""
    prospect_root, brief = _setup_prospect(tmp_path)
    _write_fake_video(
        prospect_root / "assets" / WATERMARKED_PREVIEW_FILENAME,
        size_bytes=26 * 1024 * 1024,
    )
    manifest = build_microsite("acme-skincare", brief=brief)
    warnings = manifest.get("warnings", [])
    assert any("Cloudflare" in w for w in warnings), warnings


# --------------------------------------------------------------------------- #
# Assets + manifest links
# --------------------------------------------------------------------------- #


def test_manifest_lists_assets_used(tmp_path: Path):
    _, brief = _setup_prospect(tmp_path)
    manifest = build_microsite("acme-skincare", brief=brief)
    assets = manifest["assets_used"]
    # The synthetic brief includes logo + hero + 2 product images + 1 ad screenshot.
    assert len(assets) >= 4
    for path in assets:
        assert path.startswith("assets/"), path


def test_asset_paths_copied_into_site_assets(tmp_path: Path):
    prospect_root, brief = _setup_prospect(tmp_path)
    build_microsite("acme-skincare", brief=brief)
    site_assets = prospect_root / "site" / "assets"
    assert site_assets.is_dir()
    files = list(site_assets.iterdir())
    # Five image assets in the synthetic brief; all copied (count may be
    # higher than 5 because some get renamed for collision avoidance).
    assert len(files) >= 5


def test_manifest_open_ad_links_from_brief(tmp_path: Path):
    _, brief = _setup_prospect(tmp_path)
    manifest = build_microsite("acme-skincare", brief=brief)
    links = manifest["open_ad_links"]
    assert "https://www.facebook.com/ads/library/?id=1111111111111111" in links
    assert "https://www.facebook.com/ads/library/?id=2222222222222222" in links


def test_manifest_website_url_propagated(tmp_path: Path):
    _, brief = _setup_prospect(tmp_path)
    manifest = build_microsite("acme-skincare", brief=brief)
    assert manifest["website_url"] == "https://example.com"


# --------------------------------------------------------------------------- #
# Deploy package
# --------------------------------------------------------------------------- #


def test_deploy_package_path_is_generated(tmp_path: Path):
    _, brief = _setup_prospect(tmp_path)
    manifest = build_microsite(
        "acme-skincare",
        brief=brief,
        prospects_root=tmp_path / "prospects",
    )
    deploy_dir = build_deploy_package(
        "acme-skincare",
        prospects_root=tmp_path / "prospects",
        build_root=tmp_path / "build",
    )
    assert deploy_dir.exists()
    assert deploy_dir.is_dir()
    # Path lives under build/pitches/p/<slug>/
    rel = deploy_dir.relative_to((tmp_path / "build").resolve())
    assert rel.parts[:3] == ("pitches", "p", manifest["private_slug"])


def test_deploy_package_contains_index_and_assets(tmp_path: Path):
    _, brief = _setup_prospect(tmp_path)
    build_microsite(
        "acme-skincare", brief=brief, prospects_root=tmp_path / "prospects"
    )
    deploy_dir = build_deploy_package(
        "acme-skincare",
        prospects_root=tmp_path / "prospects",
        build_root=tmp_path / "build",
    )
    assert (deploy_dir / "index.html").is_file()
    assert (deploy_dir / "assets").is_dir()
    assert (deploy_dir / "manifest.json").is_file()
    # Index references assets with relative paths (deploy folder is self-contained).
    html = (deploy_dir / "index.html").read_text(encoding="utf-8")
    assert 'src="assets/' in html


def test_deploy_package_overwrites_existing(tmp_path: Path):
    """Re-running deploy package should not error when the target dir exists."""
    _, brief = _setup_prospect(tmp_path)
    build_microsite(
        "acme-skincare", brief=brief, prospects_root=tmp_path / "prospects"
    )
    d1 = build_deploy_package(
        "acme-skincare",
        prospects_root=tmp_path / "prospects",
        build_root=tmp_path / "build",
    )
    d2 = build_deploy_package(
        "acme-skincare",
        prospects_root=tmp_path / "prospects",
        build_root=tmp_path / "build",
    )
    assert d1 == d2
    assert d2.exists()


def test_deploy_package_fails_without_manifest(tmp_path: Path):
    """build_deploy_package() requires the microsite to have been built first."""
    import pytest
    (tmp_path / "prospects" / "untouched").mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        build_deploy_package(
            "untouched",
            prospects_root=tmp_path / "prospects",
            build_root=tmp_path / "build",
        )


# --------------------------------------------------------------------------- #
# No public index discipline
# --------------------------------------------------------------------------- #


def test_no_public_prospect_index_exists_in_repo():
    """The repo must not ship a tracked index page that lists every prospect.

    Acts as a guardrail against accidentally publishing a directory of
    private URLs. Looks for likely culprits at common roots.
    """
    repo_root = Path(__file__).resolve().parents[1]
    for relative in (
        "prospects/index.html",
        "build/pitches/index.html",
        "build/pitches/p/index.html",
    ):
        candidate = repo_root / relative
        assert not candidate.exists(), (
            f"refusing to ship a tracked public prospect index at {relative}"
        )


def test_microsite_renders_brand_name_on_cover(tmp_path: Path):
    """Smoke check that the regular deck content still renders inside the site."""
    prospect_root, brief = _setup_prospect(tmp_path)
    build_microsite("acme-skincare", brief=brief)
    html = (prospect_root / "site" / "index.html").read_text(encoding="utf-8")
    assert "Acme Skincare" in html
    assert "Private Creative Note" in html


# --------------------------------------------------------------------------- #
# Bonus: a hand-edited manifest token of the wrong shape is rejected and replaced
# --------------------------------------------------------------------------- #


def test_corrupted_manifest_token_is_regenerated(tmp_path: Path):
    """If a manifest has a malformed slug, the builder mints a fresh one."""
    prospect_root, brief = _setup_prospect(tmp_path)
    site_dir = prospect_root / "site"
    site_dir.mkdir(parents=True, exist_ok=True)
    # Drop in a manifest with an out-of-spec token.
    (site_dir / "manifest.json").write_text(
        json.dumps({"private_slug": "acme-skincare-BADTOKEN!"}),
        encoding="utf-8",
    )
    # We're poking the assets folder indirectly; make sure raw assets exist for the build.
    _make_real_png(prospect_root / "assets" / "x.png")
    manifest = build_microsite("acme-skincare", brief=brief)
    new_token = manifest["private_slug"].rsplit("-", 1)[-1]
    assert len(new_token) == TOKEN_LENGTH
    assert all(c in TOKEN_ALPHABET for c in new_token)
