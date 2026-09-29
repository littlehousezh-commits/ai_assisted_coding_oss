#!/usr/bin/env python3
"""
Supplementary analysis code for the vibe-coding OSS study.

This file consolidates the final analysis logic corresponding to the paper's
methodology and results. It intentionally excludes exploratory and deprecated
notebook fragments.

Canonical cohorts used in the paper:
  - RQ1 full filtered sample: 1,240 repositories.
  - RQ2 main comparison sample: 608 repositories whose first observed AI chat
    occurred strictly after GitHub publication.
  - RQ2 validation sample: 114 older repositories created before 2025.

Important construct definitions:
  - AI-related commit: an artifact-containing commit, i.e., a commit matched
    from the GitHub blob URL/ref for a chat-history file and present in the
    collected commit history.
  - AI-related development commit: an AI-related commit that also modifies at
    least one development file after excluding AI-chat artifact files.
  - Subsequent activity for chat-purpose analysis: the chat-associated commit
    matched from the chat-history file's GitHub blob URL/ref. It is an observable chat-associated commit.
  - Relative month: calendar month re-indexed around the first observed AI-chat
    timestamp; adoption month is 0 and is excluded from pre/post comparisons.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.multitest import multipletests

try:
    import statsmodels.api as sm
    import statsmodels.formula.api as smf
except Exception:  # pragma: no cover - statsmodels is needed for model outputs.
    sm = None
    smf = None


ROOT = Path(os.environ.get("VIBE_CODING_ROOT", Path(__file__).resolve().parent)).resolve()
CLEAN = ROOT / "clean_data"
REPO_ACTIVITY = ROOT / "data_complete" / "repo_activity"
OUTPUTS = ROOT / "outputs"
SUPP_OUT = OUTPUTS / "supplementary_reproducible_analysis"

COHORT_DIR = OUTPUTS / "toy_homework_audit"
FILTERED_ALL = COHORT_DIR / "complete_history_repos_excluding_strict_name_toy_homework_1240.csv"
FILTERED_MAIN = COHORT_DIR / "main_comparison_chat_after_start_excluding_toy_homework_608.csv"
FILTERED_OLD = COHORT_DIR / "older_pre2025_excluding_toy_homework_114.csv"

BUG_FIX_RE = re.compile(r"\b(fix|bug|defect|fault|error|issue|patch|hotfix|regression)\b", re.I)
REVERT_RE = re.compile(r"\b(revert|reverted|rollback|back\s*out|backout)\b", re.I)
BUG_LABEL_RE = re.compile(r"\b(bug|defect|regression|error)\b", re.I)
BOT_RE = re.compile(r"(\[bot\]|bot$|github-actions|dependabot|renovate)", re.I)
DEV_CATEGORIES = {"source_code", "test", "documentation", "dependency", "config_build", "generated_binary", "other"}
AI_ARTIFACT_CATEGORIES = {"ai_chat_artifact", "chat_history_artifact"}


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_csv(path: Path, **kwargs: Any) -> pd.DataFrame:
    return pd.read_csv(path, low_memory=False, **kwargs)


def to_utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True, format="mixed")


def repo_key_series(df: pd.DataFrame) -> pd.Series:
    if "repo_full_name" in df.columns:
        return df["repo_full_name"].astype(str)
    if "full_name" in df.columns:
        return df["full_name"].astype(str)
    if "repo_name" in df.columns:
        return df["repo_name"].astype(str)
    raise KeyError("No repository identifier column found")


def normalize_repo_column(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "repo_key" not in out.columns:
        out["repo_key"] = repo_key_series(out)
    return out


def load_canonical_cohorts() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load final toy/homework-filtered cohorts used in the paper."""
    all_repos = normalize_repo_column(read_csv(FILTERED_ALL))
    main = normalize_repo_column(read_csv(FILTERED_MAIN))
    old = normalize_repo_column(read_csv(FILTERED_OLD))
    assert all_repos["repo_key"].nunique() == 1240, "Expected 1,240 filtered repositories"
    assert main["repo_key"].nunique() == 608, "Expected 608 main comparison repositories"
    assert old["repo_key"].nunique() == 114, "Expected 114 older validation repositories"
    return all_repos, main, old


def load_clean_tables() -> dict[str, pd.DataFrame]:
    """Load cleaned tables; aliases cover naming differences across snapshots."""
    candidates = {
        "sessions": ["chat_sessions.csv"],
        "commits": ["repository_commit_activity.csv", "repo_commits_with_churn_full.csv", "repo_commits_full.csv"],
        "commit_files": ["repository_commit_files.csv", "repo_commit_files_full.csv"],
        "metadata": ["repository_metadata.csv", "repo_metadata.csv"],
        "issues": ["repo_issues_full.csv", "repository_issues.csv"],
        "pulls": ["repo_pull_requests_full.csv", "repository_pull_requests.csv"],
        "comments": ["repo_comments_full.csv", "repository_comments.csv"],
        "reviews": ["repo_pr_reviews_full.csv", "repository_pr_reviews.csv"],
        "ci": ["repo_ci_checks_full.csv", "repository_ci_checks.csv"],
    }
    tables: dict[str, pd.DataFrame] = {}
    for name, names in candidates.items():
        search_roots = [CLEAN, REPO_ACTIVITY] if name == "sessions" else [REPO_ACTIVITY, CLEAN]
        for root in search_roots:
            for rel in names:
                path = root / rel
                if path.exists():
                    tables[name] = read_csv(path)
                    break
            if name in tables:
                break
    return tables


def extract_commit_or_ref_from_blob_url(url: Any) -> str | None:
    if pd.isna(url):
        return None
    parts = [p for p in urlparse(str(url)).path.split("/") if p]
    if len(parts) >= 5 and parts[2] == "blob":
        return parts[3]
    return None


def standardize_file_category(value: Any) -> str:
    if pd.isna(value):
        return "other"
    text = str(value).strip().lower()
    aliases = {
        "source": "source_code",
        "source_file": "source_code",
        "source_code": "source_code",
        "test": "test",
        "tests": "test",
        "documentation": "documentation",
        "docs": "documentation",
        "dependency": "dependency",
        "dependencies": "dependency",
        "config": "config_build",
        "configuration": "config_build",
        "config_build": "config_build",
        "generated": "generated_binary",
        "generated_or_binary": "generated_binary",
        "generated_binary": "generated_binary",
        "chat_history_artifact": "ai_chat_artifact",
        "ai_chat_artifact": "ai_chat_artifact",
    }
    return aliases.get(text, "other")


def add_file_category_flags(files: pd.DataFrame) -> pd.DataFrame:
    out = normalize_repo_column(files)
    if "path_category" in out.columns:
        out["file_category"] = out["path_category"].map(standardize_file_category)
    elif "file_category" not in out.columns:
        out["file_category"] = "other"
    out["is_source"] = out["file_category"].eq("source_code") | out.get("is_source_file", False).fillna(False).astype(bool)
    out["is_test"] = out["file_category"].eq("test") | out.get("is_test_file", False).fillna(False).astype(bool)
    out["is_ai_artifact"] = out["file_category"].isin(AI_ARTIFACT_CATEGORIES)
    out["is_development_file"] = out["file_category"].isin(DEV_CATEGORIES)
    for col in ["additions", "deletions", "changes"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)
    return out


def add_commit_flags(commits: pd.DataFrame, files: pd.DataFrame) -> pd.DataFrame:
    commits = normalize_repo_column(commits).copy()
    files = add_file_category_flags(files)
    if "commit_timestamp" in commits.columns:
        commits["commit_time"] = to_utc(commits["commit_timestamp"])
    elif "author_timestamp" in commits.columns:
        commits["commit_time"] = to_utc(commits["author_timestamp"])
    else:
        raise KeyError("Commit table needs commit_timestamp or author_timestamp")
    commits["commit_message"] = commits.get("commit_message", "").fillna("").astype(str)
    commits["is_bug_fix_commit"] = commits["commit_message"].str.contains(BUG_FIX_RE, na=False)
    commits["is_revert_commit"] = commits["commit_message"].str.contains(REVERT_RE, na=False)

    agg = (
        files.groupby(["repo_key", "commit_sha"], dropna=False)
        .agg(
            has_source=("is_source", "any"),
            has_test=("is_test", "any"),
            has_ai_artifact=("is_ai_artifact", "any"),
            has_development_file=("is_development_file", "any"),
            total_additions=("additions", "sum"),
            total_deletions=("deletions", "sum"),
            total_changes=("changes", "sum"),
            files_changed=("file_path", "nunique"),
            source_files_changed=("is_source", "sum"),
        )
        .reset_index()
    )
    agg["total_churn"] = agg["total_additions"] + agg["total_deletions"]
    out = commits.merge(agg, on=["repo_key", "commit_sha"], how="left")
    for col in ["has_source", "has_test", "has_ai_artifact", "has_development_file"]:
        out[col] = out[col].fillna(False).astype(bool)
    out["is_source_commit"] = out["has_source"]
    out["is_substantive_commit"] = out["has_development_file"]
    return out


def build_ai_related_commits(sessions: pd.DataFrame, commits: pd.DataFrame, files: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Identify artifact-containing and mixed AI-artifact development commits.

    The session table should contain either a `commit_or_ref` column or a
    GitHub blob URL column (`search_html_url`, `chat_blob_url`, or `html_url`).
    The ref is matched against collected commit SHAs in the mapped repository.
    """
    sessions = normalize_repo_column(sessions).copy()
    commits = add_commit_flags(commits, files)
    if "commit_or_ref" not in sessions.columns:
        url_col = next((c for c in ["chat_blob_url", "search_html_url", "html_url"] if c in sessions.columns), None)
        sessions["commit_or_ref"] = sessions[url_col].map(extract_commit_or_ref_from_blob_url) if url_col else pd.NA
    sessions["session_time"] = to_utc(
        sessions.get("session_timestamp", sessions.get("session_timestamp_heuristic", pd.Series(index=sessions.index)))
    )

    linked = sessions.merge(
        commits,
        left_on=["repo_key", "commit_or_ref"],
        right_on=["repo_key", "commit_sha"],
        how="left",
        suffixes=("_session", "_commit"),
    )
    linked["has_chat_associated_commit"] = linked["commit_sha"].notna()
    linked["is_ai_related_commit"] = linked["has_chat_associated_commit"]
    linked["is_ai_related_development_commit"] = linked["is_ai_related_commit"] & linked["has_development_file"]

    ai_commit_keys = linked.loc[linked["is_ai_related_commit"], ["repo_key", "commit_sha"]].drop_duplicates()
    ai_dev_keys = linked.loc[linked["is_ai_related_development_commit"], ["repo_key", "commit_sha"]].drop_duplicates()
    commits = commits.merge(ai_commit_keys.assign(is_ai_related_commit=True), on=["repo_key", "commit_sha"], how="left")
    commits = commits.merge(ai_dev_keys.assign(is_ai_related_development_commit=True), on=["repo_key", "commit_sha"], how="left")
    commits["is_ai_related_commit"] = commits["is_ai_related_commit"].fillna(False)
    commits["is_ai_related_development_commit"] = commits["is_ai_related_development_commit"].fillna(False)
    return commits, linked


def repository_adoption_times(sessions: pd.DataFrame) -> pd.DataFrame:
    sessions = normalize_repo_column(sessions).copy()
    time_col = "session_timestamp" if "session_timestamp" in sessions.columns else "session_timestamp_heuristic"
    sessions["session_time"] = to_utc(sessions[time_col])
    return (
        sessions.dropna(subset=["session_time"])
        .groupby("repo_key", as_index=False)
        .agg(first_ai_session_time=("session_time", "min"))
    )


def relative_month(event_time: pd.Series, adoption_time: pd.Series) -> pd.Series:
    event_period = event_time.dt.to_period("M")
    adoption_period = adoption_time.dt.to_period("M")
    return (event_period.dt.year - adoption_period.dt.year) * 12 + (event_period.dt.month - adoption_period.dt.month)


def add_period(df: pd.DataFrame, time_col: str, adoption: pd.DataFrame) -> pd.DataFrame:
    out = normalize_repo_column(df).merge(adoption, on="repo_key", how="left")
    out[time_col] = to_utc(out[time_col])
    out["relative_month"] = relative_month(out[time_col], out["first_ai_session_time"])
    out["period"] = np.where(out[time_col] < out["first_ai_session_time"], "pre", "post")
    out.loc[out["relative_month"].eq(0), "period"] = "adoption_month"
    return out


def wilcoxon_pre_post(values: pd.DataFrame, measure: str, cohort: str) -> dict[str, Any]:
    paired = values.pivot_table(index="repo_key", columns="period", values=measure, aggfunc="first")
    paired = paired.reindex(columns=["pre", "post"]).dropna(subset=["pre", "post"])
    if paired.empty:
        return {"cohort": cohort, "measure": measure, "n": 0}
    diffs = paired["post"] - paired["pre"]
    try:
        stat, p = stats.wilcoxon(paired["post"], paired["pre"], zero_method="wilcox") if diffs.ne(0).any() else (0.0, 1.0)
    except ValueError:
        stat, p = np.nan, 1.0
    return {
        "cohort": cohort,
        "measure": measure,
        "n": int(len(paired)),
        "pre_mean": float(paired["pre"].mean()),
        "post_mean": float(paired["post"].mean()),
        "pre_median": float(paired["pre"].median()),
        "post_median": float(paired["post"].median()),
        "median_change": float(diffs.median()),
        "pct_increase": float((diffs > 0).mean()),
        "pct_decrease": float((diffs < 0).mean()),
        "p": float(p),
    }


def add_bh_q(rows: list[dict[str, Any]]) -> pd.DataFrame:
    out = pd.DataFrame(rows)
    if "p" in out.columns and out["p"].notna().any():
        mask = out["p"].notna()
        out.loc[mask, "q"] = multipletests(out.loc[mask, "p"], method="fdr_bh")[1]
    return out


def fit_its(panel, outcome, cohort):
    """Repository fixed effects, age adjustment and clustered uncertainty."""
    transform = "identity" if outcome.endswith(("_share", "_rate")) else "log1p"
    data = panel[panel.relative_month.ne(0)]
    rows = sensitivity_within_cluster_fit(data, outcome, transform)
    return pd.DataFrame([dict(cohort=cohort, outcome=outcome, **r) for r in rows])



def rq1_ai_use(tables: dict[str, pd.DataFrame], all_repos: pd.DataFrame) -> None:
    sessions = tables["sessions"]
    commits, linked_sessions = build_ai_related_commits(sessions, tables["commits"], tables["commit_files"])
    files = add_file_category_flags(tables["commit_files"])
    adoption = repository_adoption_times(sessions)
    repos = set(all_repos["repo_key"])
    commits = commits[commits["repo_key"].isin(repos)].merge(adoption, on="repo_key", how="left")
    commits = commits[commits["commit_time"] >= commits["first_ai_session_time"]]

    repo_summary = (
        commits.groupby("repo_key")
        .agg(
            post_adoption_commits=("commit_sha", "nunique"),
            ai_related_commits=("is_ai_related_commit", "sum"),
            ai_related_development_commits=("is_ai_related_development_commit", "sum"),
        )
        .reset_index()
    )
    repo_summary["ai_related_commit_share"] = repo_summary["ai_related_commits"] / repo_summary["post_adoption_commits"]
    repo_summary.to_csv(SUPP_OUT / "rq1_repo_ai_related_commit_summary.csv", index=False)

    commits["relative_month"] = relative_month(commits["commit_time"], commits["first_ai_session_time"])
    month = (
        commits.groupby("relative_month")
        .agg(commits=("commit_sha", "nunique"), ai_related=("is_ai_related_commit", "sum"))
        .reset_index()
    )
    month["ai_related_commit_share"] = month["ai_related"] / month["commits"]
    month.to_csv(SUPP_OUT / "rq1_ai_related_commit_share_by_relative_month.csv", index=False)

    # File-type composition among development files in AI-related development commits.
    ai_keys = commits.loc[commits["is_ai_related_development_commit"], ["repo_key", "commit_sha"]].drop_duplicates()
    ai_files = files.merge(ai_keys, on=["repo_key", "commit_sha"], how="inner")
    dev_files = ai_files[~ai_files["is_ai_artifact"]].copy()
    file_dist = dev_files["file_category"].value_counts(normalize=True).rename_axis("file_category").reset_index(name="share")
    file_dist.to_csv(SUPP_OUT / "rq1_ai_related_development_file_type_distribution.csv", index=False)

    # Concentration by file and top-level module, per repository.
    dev_files["module"] = dev_files["file_path"].fillna("(missing)").astype(str).str.replace("\\", "/", regex=False).map(
        lambda p: p.split("/", 1)[0] if "/" in p else "(root)"
    )
    concentration = []
    for repo, grp in dev_files.groupby("repo_key"):
        for level, col in [("file", "file_path"), ("module", "module")]:
            counts = grp[col].value_counts()
            shares = counts / counts.sum()
            concentration.append(
                {
                    "repo_key": repo,
                    "level": level,
                    "unique_units": int(len(counts)),
                    "top1_share": float(shares.iloc[0]) if len(shares) else np.nan,
                    "top3_share": float(shares.iloc[:3].sum()) if len(shares) else np.nan,
                    "hhi": float((shares**2).sum()) if len(shares) else np.nan,
                }
            )
    pd.DataFrame(concentration).to_csv(SUPP_OUT / "rq1_ai_related_file_module_concentration.csv", index=False)
    linked_sessions.to_csv(SUPP_OUT / "rq1_chat_associated_commit_session_level.csv", index=False)


def load_chat_labels() -> pd.DataFrame:
    candidates = [
        OUTPUTS / "chat_label_correction_1240" / "combined_session_dominant_labels_1240.csv",
        OUTPUTS / "chat_label_correction_1240" / "combined_seven_category_session_distribution_1240.csv",
    ]
    for path in candidates:
        if path.exists():
            return read_csv(path)
    return pd.DataFrame()


def rq1_chat_purpose(tables: dict[str, pd.DataFrame], all_repos: pd.DataFrame) -> None:
    labels = load_chat_labels()
    if labels.empty:
        return
    labels = labels.rename(columns={"dominant_category": "chat_purpose", "category": "chat_purpose"})
    if "session_sha" not in labels.columns or "chat_purpose" not in labels.columns:
        return
    sessions = normalize_repo_column(tables["sessions"])
    labeled = sessions.merge(labels[["session_sha", "chat_purpose"]].drop_duplicates(), on="session_sha", how="inner")
    labeled = labeled[labeled["repo_key"].isin(set(all_repos["repo_key"]))]
    labeled.groupby("repo_key")["chat_purpose"].nunique().reset_index(name="distinct_chat_purposes").to_csv(
        SUPP_OUT / "rq1_distinct_chat_purposes_per_repo.csv", index=False
    )
    dominant = (
        labeled.groupby(["repo_key", "chat_purpose"]).size().reset_index(name="sessions")
        .sort_values(["repo_key", "sessions"], ascending=[True, False])
        .drop_duplicates("repo_key")
    )
    dominant.to_csv(SUPP_OUT / "rq1_repository_dominant_chat_purpose.csv", index=False)

    commits, linked = build_ai_related_commits(labeled, tables["commits"], tables["commit_files"])
    files = add_file_category_flags(tables["commit_files"])
    linked_dev = linked[linked["is_ai_related_development_commit"]].copy()
    linked_dev.to_csv(SUPP_OUT / "rq1_chat_purpose_associated_development_commits.csv", index=False)

    # Summarize file-type composition of chat-associated development commits.
    associated_keys = linked_dev[["session_sha", "repo_key", "commit_sha", "chat_purpose"]].drop_duplicates()
    associated_files = files.merge(associated_keys, on=["repo_key", "commit_sha"], how="inner")
    associated_files = associated_files[~associated_files["is_ai_artifact"]]
    file_types = (
        associated_files.groupby(["chat_purpose", "file_category"]).size().reset_index(name="file_changes")
    )
    file_types["share_within_purpose"] = file_types["file_changes"] / file_types.groupby("chat_purpose")["file_changes"].transform("sum")
    file_types.to_csv(SUPP_OUT / "rq1_chat_purpose_associated_file_types.csv", index=False)


def build_relative_month_commit_panel(commits: pd.DataFrame, adoption: pd.DataFrame, repos: pd.DataFrame, metadata: pd.DataFrame | None = None) -> pd.DataFrame:
    commits = commits[commits["repo_key"].isin(set(repos["repo_key"]))].copy()
    commits = commits.merge(adoption, on="repo_key", how="left")
    commits = commits.dropna(subset=["first_ai_session_time", "commit_time"])
    commits["relative_month"] = relative_month(commits["commit_time"], commits["first_ai_session_time"])
    grp = (
        commits.groupby(["repo_key", "relative_month"])
        .agg(
            total_commits=("commit_sha", "nunique"),
            substantive_commits=("is_substantive_commit", "sum"),
            source_commits=("is_source_commit", "sum"),
            bug_fix_commits=("is_bug_fix_commit", "sum"),
            revert_commits=("is_revert_commit", "sum"),
            test_touching_commits=("has_test", "sum"),
        )
        .reset_index()
    )
    if metadata is None or "collection_finished_at" not in metadata:
        raise ValueError("Repository metadata needs created_at and collection_finished_at to retain zero-activity months.")
    meta = normalize_repo_column(metadata)[["repo_key", "collection_finished_at"]].drop_duplicates("repo_key")
    cohort = repos[["repo_key", "created_at"]].merge(adoption, on="repo_key", how="left").merge(meta, on="repo_key", how="left")
    cohort["created_at"] = to_utc(cohort.created_at)
    cohort["adoption_time"] = to_utc(cohort.first_ai_session_time)
    cohort["collection_end"] = to_utc(cohort.collection_finished_at)
    cohort["collection_end"] = cohort.collection_end.fillna(to_utc(meta.collection_finished_at).max())
    grid = issue_monthly_build_observation_grid(cohort, "")
    grp = grid.merge(grp, on=["repo_key", "relative_month"], how="left")
    counts = ["total_commits", "substantive_commits", "source_commits", "bug_fix_commits", "revert_commits", "test_touching_commits"]
    grp[counts] = grp[counts].fillna(0)
    for target, numerator in [("bug_fix_commit_share", "bug_fix_commits"), ("revert_commit_share", "revert_commits"), ("test_touching_commit_share", "test_touching_commits")]:
        grp[target] = grp[numerator] / grp.total_commits.replace(0, np.nan)

    return grp


def average_pre_post_from_panel(panel: pd.DataFrame, measures: list[str], cohort: str) -> pd.DataFrame:
    panel = panel[~panel["relative_month"].eq(0)].copy()
    panel["period"] = np.where(panel["relative_month"] < 0, "pre", "post")
    repo_period = panel.groupby(["repo_key", "period"])[measures].mean().reset_index()
    rows = [wilcoxon_pre_post(repo_period, measure, cohort) for measure in measures]
    return add_bh_q(rows)


def rq2_commit_activity_and_quality(tables: dict[str, pd.DataFrame], main: pd.DataFrame, old: pd.DataFrame) -> None:
    sessions = tables["sessions"]
    adoption = repository_adoption_times(sessions)
    commits, _ = build_ai_related_commits(sessions, tables["commits"], tables["commit_files"])
    metadata = tables.get("metadata")
    rows = []
    its_rows = []
    for cohort_name, repos in [("main_608", main), ("older_114", old)]:
        panel = build_relative_month_commit_panel(commits, adoption, repos, metadata)
        panel.to_csv(SUPP_OUT / f"rq2_relative_month_commit_quality_panel_{cohort_name}.csv", index=False)
        measures = [
            "total_commits", "substantive_commits", "source_commits",
            "bug_fix_commits", "bug_fix_commit_share", "revert_commit_share",
            "test_touching_commit_share",
        ]
        rows.append(average_pre_post_from_panel(panel, measures, cohort_name))
        for outcome in ["total_commits", "substantive_commits", "source_commits", "bug_fix_commits", "bug_fix_commit_share"]:
            its_rows.append(fit_its(panel, outcome, cohort_name))
    pd.concat(rows, ignore_index=True).to_csv(SUPP_OUT / "rq2_commit_quality_pre_post_tests.csv", index=False)
    if its_rows:
        add_bh_q(pd.concat(its_rows, ignore_index=True).to_dict("records")).to_csv(
            SUPP_OUT / "rq2_commit_quality_its_models.csv", index=False
        )


def issue_is_bug(labels: Any) -> bool:
    if pd.isna(labels):
        return False
    return bool(BUG_LABEL_RE.search(str(labels)))


def rq2_issues_prs(tables, main, old):
    """Monthly count rates plus separately defined period-volume ratios."""
    configure_updated_rq2()
    issue_monthly_main()
    # Shares retain complete pre/post periods split at the observed timestamp,
    # as in the paper's whole-period share analysis; count-rate inference above
    # separately excludes relative month zero.
    adoption = repository_adoption_times(tables["sessions"])
    frames = []
    for key, ident, events in [
        ("issues", "issue_id", [("created_at", "issues_opened"), ("closed_at", "issues_closed")]),
        ("pulls", "pr_id", [("created_at", "prs_opened"), ("merged_at", "prs_merged")]),
    ]:
        data = normalize_repo_column(tables[key]).copy()
        if key == "issues" and "is_pull_request" in data:
            data = data[~data.is_pull_request.astype(str).str.lower().isin(["true", "1"])]
        data = data.drop_duplicates(["repo_key", ident]).merge(adoption, on="repo_key", how="left")
        for time, measure in events:
            d = data.copy(); d[time] = to_utc(d[time]); d = d.dropna(subset=[time, "first_ai_session_time"])
            d["period"] = np.where(d[time] < d.first_ai_session_time, "pre", "post")
            frames.append(d.groupby(["repo_key", "period"])[ident].nunique().rename(measure))
    counts = pd.concat(frames, axis=1).fillna(0)
    tests = []
    for cohort, repos in [("main_608", main), ("older_114", old)]:
        index = pd.MultiIndex.from_product([repos.repo_key, ["pre", "post"]], names=["repo_key", "period"])
        values = counts.reindex(index, fill_value=0).reset_index()
        opened = (values.issues_opened + values.prs_opened).replace(0, np.nan)
        values["issue_open_share"] = values.issues_opened / opened
        values["pr_open_share"] = values.prs_opened / opened
        values["issue_close_share"] = values.issues_closed / values.issues_opened.replace(0, np.nan)
        values["pr_merge_share"] = values.prs_merged / values.prs_opened.replace(0, np.nan)
        measures = ["issue_open_share", "pr_open_share", "issue_close_share", "pr_merge_share"]
        tests.append(add_bh_q([wilcoxon_pre_post(values, m, cohort) for m in measures]))
        values.to_csv(SUPP_OUT / f"rq2_issue_pr_whole_period_values_{cohort}.csv", index=False)
    pd.concat(tests, ignore_index=True).to_csv(SUPP_OUT / "rq2_issue_pr_whole_period_tests.csv", index=False)
    survival_main()



def hhi_from_counts(counts: pd.Series) -> float:
    total = counts.sum()
    if total <= 0:
        return np.nan
    shares = counts / total
    return float((shares**2).sum())


def top_share(counts: pd.Series) -> float:
    total = counts.sum()
    return float(counts.max() / total) if total > 0 else np.nan


def rq2_collaboration_review(tables, main, old):
    """Zero-filled monthly human participation and active-month concentration."""
    configure_updated_rq2()
    collaboration_main()



LIKERT_MAP = {
    "Strongly disagree": 1,
    "Somewhat disagree": 2,
    "Neither agree nor disagree": 3,
    "Somewhat agree": 4,
    "Strongly agree": 5,
}


def survey_likert_to_num(series: pd.Series) -> pd.Series:
    return series.map(lambda x: LIKERT_MAP.get(str(x).strip(), np.nan))


def rq3_survey(survey_path: Path) -> None:
    if not survey_path.exists():
        return
    raw = pd.read_csv(survey_path, dtype=str)
    df = raw.iloc[2:].copy() if len(raw) > 2 else raw.copy()
    finished = df[(df.get("Finished") == "True") & (df.get("Progress") == "100")].copy()
    valid = finished if not finished.empty else df

    # Demographics.
    demo_cols = {
        "Q1": "age",
        "Q2": "gender",
        "Q3": "role",
        "Q4": "programming_experience",
        "Q5": "years_programming",
        "Q6": "oss_experience",
        "Q7": "oss_contribution_frequency",
        "Q8": "vibe_coding_familiarity",
        "Q9": "vibe_coding_use_for_oss",
    }
    demo_rows = []
    for col, label in demo_cols.items():
        if col not in valid.columns:
            continue
        counts = valid[col].dropna().value_counts()
        total = counts.sum()
        for category, count in counts.items():
            demo_rows.append({"measure": label, "category": category, "n": int(count), "share": float(count / total)})
    pd.DataFrame(demo_rows).to_csv(SUPP_OUT / "rq3_survey_demographics.csv", index=False)

    # Likert item summaries and constructs used in the paper.
    likert_cols = [c for c in valid.columns if re.match(r"Q(11|12|13|14|15)_\d+", c)]
    item_rows = []
    scores = pd.DataFrame(index=valid.index)
    for col in likert_cols:
        score = survey_likert_to_num(valid[col])
        scores[col] = score
        item_rows.append(
            {
                "item": col,
                "n": int(score.notna().sum()),
                "mean": float(score.mean()),
                "median": float(score.median()),
                "pct_somewhat_or_strongly_agree": float(score.ge(4).mean()),
            }
        )
    pd.DataFrame(item_rows).to_csv(SUPP_OUT / "rq3_survey_likert_item_summary.csv", index=False)

    constructs = {
        "oss_contribution_access": ["Q11_3", "Q11_4", "Q11_5", "Q11_6"],
        "own_ai_code_concern": ["Q12_1", "Q12_2", "Q12_4", "Q12_6", "Q12_7"],
        "others_ai_code_concern": ["Q13_1", "Q13_2", "Q13_4", "Q13_6", "Q13_7"],
        "social_image_concern": ["Q14_1", "Q14_2", "Q14_3", "Q14_4", "Q14_5", "Q14_8"],
        "responsibility_governance": ["Q15_3", "Q15_4"],
    }
    construct_rows = []
    for name, cols in constructs.items():
        available = [c for c in cols if c in scores.columns]
        if not available:
            continue
        score = scores[available].mean(axis=1)
        try:
            _, p = stats.wilcoxon(score.dropna() - 3)
        except ValueError:
            p = np.nan
        construct_rows.append(
            {
                "construct": name,
                "items": ";".join(available),
                "n": int(score.notna().sum()),
                "mean": float(score.mean()),
                "median": float(score.median()),
                "pct_above_neutral": float(score.gt(3).mean()),
                "p_vs_neutral": p,
            }
        )
    pd.DataFrame(construct_rows).to_csv(SUPP_OUT / "rq3_survey_construct_summary.csv", index=False)


# RQ2 ISSUE PR MONTHLY RATES
import math
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats
import statsmodels.formula.api as smf
from statsmodels.stats.multitest import multipletests
issue_monthly_ROOT = Path(__file__).resolve().parents[1]
issue_monthly_DATA = issue_monthly_ROOT / 'data_complete' / 'repo_activity'
issue_monthly_COHORT_DIR = issue_monthly_ROOT / 'outputs' / 'toy_homework_audit'
issue_monthly_OUT = issue_monthly_ROOT / 'outputs' / 'rq_issue_pr_filtered_1240' / 'rate_recalc'
issue_monthly_COHORTS = {'main_608': issue_monthly_COHORT_DIR / 'main_comparison_chat_after_start_excluding_toy_homework_608.csv', 'older_114': issue_monthly_COHORT_DIR / 'older_pre2025_excluding_toy_homework_114.csv'}
issue_monthly_MEASURES = ['issues_opened', 'issues_closed', 'prs_opened', 'prs_merged']

def issue_monthly_to_utc(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, utc=True, errors='coerce', format='mixed')

def issue_monthly_repo_key(values: pd.Series) -> pd.Series:
    return values.astype(str).str.strip().str.lower()

def issue_monthly_build_observation_grid(cohort: pd.DataFrame, cohort_name: str) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for row in cohort.itertuples(index=False):
        if pd.isna(row.created_at) or pd.isna(row.adoption_time) or pd.isna(row.collection_end):
            continue
        start = row.created_at.tz_convert(None).to_period('M')
        end = row.collection_end.tz_convert(None).to_period('M')
        adoption_month = row.adoption_time.tz_convert(None).to_period('M')
        for month in pd.period_range(start=start, end=end, freq='M'):
            relative_month = month.ordinal - adoption_month.ordinal
            month_start = month.start_time.tz_localize('UTC')
            rows.append({'cohort': cohort_name, 'repo_key': row.repo_key, 'calendar_month': str(month), 'relative_month': relative_month, 'period': 'pre' if relative_month < 0 else 'post' if relative_month > 0 else 'adoption', 'repo_age_months': max(0.0, (month_start - row.created_at).total_seconds() / (86400 * 30.4375))})
    grid = pd.DataFrame(rows)
    if grid.duplicated(['cohort', 'repo_key', 'calendar_month']).any():
        raise ValueError('Observation grid contains duplicate repository-months')
    return grid

def issue_monthly_aggregate_events(frame: pd.DataFrame, time_column: str, id_column: str, output_column: str) -> pd.DataFrame:
    events = frame.dropna(subset=[time_column]).copy()
    events['calendar_month'] = events[time_column].dt.tz_convert(None).dt.to_period('M').astype(str)
    return events.groupby(['repo_key', 'calendar_month'], as_index=False)[id_column].nunique().rename(columns={id_column: output_column})

def issue_monthly_rank_biserial(pre: pd.Series, post: pd.Series) -> float:
    difference = (post - pre).dropna()
    difference = difference[difference.ne(0)]
    if difference.empty:
        return 0.0
    ranks = stats.rankdata(difference.abs())
    positive = ranks[difference.gt(0)].sum()
    negative = ranks[difference.lt(0)].sum()
    return float((positive - negative) / ranks.sum())

def issue_monthly_paired_rate_tests(panel: pd.DataFrame, cohort_name: str) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for measure in issue_monthly_MEASURES:
        values = panel.groupby(['repo_key', 'period'])[measure].mean().unstack('period').dropna(subset=['pre', 'post'])
        difference = values['post'] - values['pre']
        if difference.ne(0).any():
            (_, p_value) = stats.wilcoxon(values['post'], values['pre'], zero_method='wilcox')
        else:
            p_value = 1.0
        rows.append({'cohort': cohort_name, 'measure': measure, 'n': len(values), 'pre_mean': values['pre'].mean(), 'post_mean': values['post'].mean(), 'pre_median': values['pre'].median(), 'post_median': values['post'].median(), 'mean_change': difference.mean(), 'median_change': difference.median(), 'pct_increasing': difference.gt(0).mean() * 100, 'pct_decreasing': difference.lt(0).mean() * 100, 'pct_unchanged': difference.eq(0).mean() * 100, 'p': p_value, 'rank_biserial_r': issue_monthly_rank_biserial(values['pre'], values['post'])})
    result = pd.DataFrame(rows)
    result['q'] = multipletests(result['p'], method='fdr_bh')[1]
    return result

def issue_monthly_fit_its(panel: pd.DataFrame, cohort_name: str) -> pd.DataFrame:
    data = panel.copy()
    data['pretime'] = np.where(data['relative_month'].lt(0), data['relative_month'], 0)
    data['post'] = data['relative_month'].gt(0).astype(int)
    data['posttime'] = np.where(data['relative_month'].gt(0), data['relative_month'], 0)
    data['log_age'] = np.log1p(data['repo_age_months'].clip(lower=0))
    rows: list[dict[str, object]] = []
    for measure in issue_monthly_MEASURES:
        data['outcome'] = np.log1p(data[measure])
        model = smf.ols('outcome ~ pretime + post + posttime + log_age + C(repo_key)', data=data).fit(cov_type='cluster', cov_kwds={'groups': data['repo_key']})
        for term in ['pretime', 'post', 'posttime']:
            coefficient = float(model.params[term])
            rows.append({'cohort': cohort_name, 'measure': measure, 'term': term, 'coef': coefficient, 'effect_percent': 100 * math.expm1(coefficient), 'p': float(model.pvalues[term]), 'n_obs': int(model.nobs), 'n_repos': data['repo_key'].nunique()})
        names = list(model.params.index)
        contrast = np.zeros(len(names))
        contrast[names.index('posttime')] = 1
        contrast[names.index('pretime')] = -1
        test = model.t_test(contrast)
        coefficient = float(test.effect[0])
        rows.append({'cohort': cohort_name, 'measure': measure, 'term': 'posttime_minus_pretime', 'coef': coefficient, 'effect_percent': 100 * math.expm1(coefficient), 'p': float(test.pvalue), 'n_obs': int(model.nobs), 'n_repos': data['repo_key'].nunique()})
    result = pd.DataFrame(rows)
    result['q'] = multipletests(result['p'], method='fdr_bh')[1]
    return result

def issue_monthly_main() -> None:
    metadata = pd.read_csv(issue_monthly_DATA / 'repo_metadata.csv')
    metadata['repo_key'] = issue_monthly_repo_key(metadata['repo_full_name'])
    metadata['collection_end'] = issue_monthly_to_utc(metadata['collection_finished_at'])
    fallback_end = metadata['collection_end'].max()
    issues = pd.read_csv(issue_monthly_DATA / 'repo_issues_full.csv')
    pulls = pd.read_csv(issue_monthly_DATA / 'repo_pull_requests_full.csv')
    issues['repo_key'] = issue_monthly_repo_key(issues['repo_full_name'])
    pulls['repo_key'] = issue_monthly_repo_key(pulls['repo_full_name'])
    for column in ['created_at', 'closed_at']:
        issues[column] = issue_monthly_to_utc(issues[column])
    for column in ['created_at', 'merged_at']:
        pulls[column] = issue_monthly_to_utc(pulls[column])
    if 'is_pull_request' in issues.columns:
        is_pr = issues['is_pull_request'].astype(str).str.lower().isin({'true', '1'})
        issues = issues[~is_pr].copy()
    event_tables = [issue_monthly_aggregate_events(issues, 'created_at', 'issue_id', 'issues_opened'), issue_monthly_aggregate_events(issues, 'closed_at', 'issue_id', 'issues_closed'), issue_monthly_aggregate_events(pulls, 'created_at', 'pr_id', 'prs_opened'), issue_monthly_aggregate_events(pulls, 'merged_at', 'pr_id', 'prs_merged')]
    all_panels = []
    all_tests = []
    all_models = []
    coverage_rows = []
    for (cohort_name, cohort_path) in issue_monthly_COHORTS.items():
        cohort = pd.read_csv(cohort_path)
        cohort['repo_key'] = issue_monthly_repo_key(cohort['repo_full_name'])
        cohort['created_at'] = issue_monthly_to_utc(cohort['created_at'])
        cohort['adoption_time'] = issue_monthly_to_utc(cohort['first_ai_session_time'])
        cohort = cohort.merge(metadata[['repo_key', 'collection_end']], on='repo_key', how='left')
        cohort['collection_end'] = cohort['collection_end'].fillna(fallback_end)
        grid = issue_monthly_build_observation_grid(cohort, cohort_name)
        for events in event_tables:
            grid = grid.merge(events, on=['repo_key', 'calendar_month'], how='left')
        for measure in issue_monthly_MEASURES:
            grid[measure] = grid[measure].fillna(0).astype(int)
        inference = grid[grid['relative_month'].ne(0)].copy()
        side_count = inference.groupby('repo_key')['period'].nunique()
        eligible = side_count[side_count.eq(2)].index
        inference = inference[inference['repo_key'].isin(eligible)].copy()
        all_panels.append(grid.assign(eligible_paired=grid['repo_key'].isin(eligible)))
        all_tests.append(issue_monthly_paired_rate_tests(inference, cohort_name))
        all_models.append(issue_monthly_fit_its(inference, cohort_name))
        coverage_row: dict[str, object] = {'cohort': cohort_name, 'cohort_repositories': cohort['repo_key'].nunique(), 'paired_repositories': len(eligible), 'inference_repository_months': len(inference), 'pre_repository_months': inference['period'].eq('pre').sum(), 'post_repository_months': inference['period'].eq('post').sum()}
        for measure in issue_monthly_MEASURES:
            coverage_row[f'zero_month_share_{measure}'] = inference[measure].eq(0).mean()
        coverage_rows.append(coverage_row)
    pd.concat(all_panels, ignore_index=True).to_csv(issue_monthly_OUT / 'relative_month_issue_pr_panel_zero_filled_608_114.csv', index=False)
    pd.concat(all_tests, ignore_index=True).to_csv(issue_monthly_OUT / 'repository_issue_pr_monthly_rate_tests_608_114.csv', index=False)
    pd.concat(all_models, ignore_index=True).to_csv(issue_monthly_OUT / 'relative_month_issue_pr_its_models_608_114.csv', index=False)
    pd.DataFrame(coverage_rows).to_csv(issue_monthly_OUT / 'panel_coverage_608_114.csv', index=False)


# RQ2 ISSUE PR SURVIVAL
import math
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats
from statsmodels.duration.hazard_regression import PHReg
from statsmodels.genmod.cov_struct import Independence
from statsmodels.genmod.families import Binomial
from statsmodels.stats.multitest import multipletests
survival_ROOT = Path(__file__).resolve().parents[1]
survival_DATA = survival_ROOT / 'data_complete' / 'repo_activity'
survival_COHORT_DIR = survival_ROOT / 'outputs' / 'toy_homework_audit'
survival_OUT = survival_ROOT / 'outputs' / 'rq_issue_pr_filtered_1240' / 'survival_recalc'
survival_COHORTS = {'main_608': survival_COHORT_DIR / 'main_comparison_chat_after_start_excluding_toy_homework_608.csv', 'older_114': survival_COHORT_DIR / 'older_pre2025_excluding_toy_homework_114.csv'}

def survival_to_utc(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, utc=True, errors='coerce', format='mixed')

def survival_repo_key(values: pd.Series) -> pd.Series:
    return values.astype(str).str.strip().str.lower()

def survival_as_bool(values: pd.Series) -> pd.Series:
    return values.astype(str).str.strip().str.lower().isin({'true', '1', 'yes'})

def survival_relative_month(timestamp: pd.Series, adoption: pd.Series) -> pd.Series:
    event_month = timestamp.dt.tz_convert(None).dt.to_period('M')
    adoption_month = adoption.dt.tz_convert(None).dt.to_period('M')
    return event_month.astype('int64') - adoption_month.astype('int64')

def survival_prepare_cohort(path: Path, metadata: pd.DataFrame) -> pd.DataFrame:
    cohort = pd.read_csv(path)
    cohort['repo_key'] = survival_repo_key(cohort['repo_full_name'])
    cohort['repo_created'] = survival_to_utc(cohort['created_at'])
    cohort['adoption_time'] = survival_to_utc(cohort['first_ai_session_time'])
    cohort = cohort.merge(metadata[['repo_key', 'collection_end', 'issue_collection_complete', 'pr_collection_complete']], on='repo_key', how='left', validate='one_to_one')
    return cohort

def survival_build_item_panel(items: pd.DataFrame, cohort: pd.DataFrame, item_type: str) -> pd.DataFrame:
    if item_type == 'issue':
        item_id = 'issue_id'
        event_time = 'closed_time'
        complete_column = 'issue_collection_complete'
    else:
        item_id = 'pr_id'
        event_time = 'merged_time'
        complete_column = 'pr_collection_complete'
    eligible = cohort[survival_as_bool(cohort[complete_column])].copy()
    panel = items.merge(eligible[['repo_key', 'repo_created', 'adoption_time', 'collection_end']], on='repo_key', how='inner', validate='many_to_one')
    panel = panel.dropna(subset=['created_time', 'repo_created', 'adoption_time', 'collection_end']).copy()
    panel = panel[panel['created_time'].between(panel['repo_created'], panel['collection_end'], inclusive='both')].copy()
    panel['relative_month'] = survival_relative_month(panel['created_time'], panel['adoption_time'])
    panel = panel[panel['relative_month'].ne(0)].copy()
    panel['period'] = np.where(panel['relative_month'].lt(0), 'pre', 'post')
    panel['post'] = panel['period'].eq('post').astype(int)
    panel['administrative_censor_time'] = panel['collection_end']
    pre_mask = panel['period'].eq('pre')
    panel.loc[pre_mask, 'administrative_censor_time'] = panel.loc[pre_mask, ['adoption_time', 'collection_end']].min(axis=1)
    panel['event_time'] = panel[event_time]
    valid_event = panel['event_time'].notna() & panel['event_time'].ge(panel['created_time']) & panel['event_time'].le(panel['administrative_censor_time'])
    panel['censor_time'] = panel['administrative_censor_time']
    if item_type == 'pull_request':
        valid_unmerged_close = panel['closed_time'].notna() & panel['closed_time'].ge(panel['created_time']) & panel['closed_time'].le(panel['administrative_censor_time']) & ~valid_event
        panel.loc[valid_unmerged_close, 'censor_time'] = panel.loc[valid_unmerged_close, 'closed_time']
    panel['event'] = valid_event.astype(int)
    panel['followup_end'] = panel['censor_time']
    panel.loc[valid_event, 'followup_end'] = panel.loc[valid_event, 'event_time']
    panel['duration_days'] = (panel['followup_end'] - panel['created_time']).dt.total_seconds() / 86400
    panel = panel[panel['duration_days'].ge(0)].copy()
    panel['duration_days'] = panel['duration_days'].clip(lower=1 / 86400)
    panel['repo_age_days_at_open'] = (panel['created_time'] - panel['repo_created']).dt.total_seconds() / 86400
    panel['log_repo_age'] = np.log1p(panel['repo_age_days_at_open'].clip(lower=0))
    panel['item_type'] = item_type
    panel['item_id'] = panel[item_id].astype(str)
    return panel

def survival_kaplan_meier(group: pd.DataFrame) -> pd.DataFrame:
    ordered_times = np.sort(group.loc[group['event'].eq(1), 'duration_days'].unique())
    survival = 1.0
    variance_sum = 0.0
    rows = [{'time_days': 0.0, 'survival': 1.0, 'ci_low': 1.0, 'ci_high': 1.0, 'at_risk': len(group), 'events': 0}]
    for time in ordered_times:
        at_risk = int(group['duration_days'].ge(time).sum())
        events = int((group['duration_days'].eq(time) & group['event'].eq(1)).sum())
        if at_risk <= 0 or events <= 0:
            continue
        survival *= 1 - events / at_risk
        if at_risk > events:
            variance_sum += events / (at_risk * (at_risk - events))
        standard_error = survival * math.sqrt(variance_sum)
        rows.append({'time_days': float(time), 'survival': survival, 'ci_low': max(0.0, survival - 1.96 * standard_error), 'ci_high': min(1.0, survival + 1.96 * standard_error), 'at_risk': at_risk, 'events': events})
    return pd.DataFrame(rows)

def survival_km_value(curve: pd.DataFrame, time: float) -> float:
    observed = curve[curve['time_days'].le(time)]
    return float(observed.iloc[-1]['survival']) if not observed.empty else 1.0

def survival_km_median(curve: pd.DataFrame) -> float:
    reached = curve[curve['survival'].le(0.5)]
    return float(reached.iloc[0]['time_days']) if not reached.empty else np.nan

def survival_fit_cox(group: pd.DataFrame) -> dict[str, float | int | str]:
    model_data = group[['duration_days', 'event', 'post', 'log_repo_age', 'repo_key']].dropna().copy()
    if model_data['event'].sum() < 2 or model_data['post'].nunique() < 2:
        return {'hazard_ratio': np.nan, 'ci_low': np.nan, 'ci_high': np.nan, 'p': np.nan, 'ph_test_p': np.nan, 'n_items': len(model_data), 'n_events': int(model_data['event'].sum()), 'n_repos': model_data['repo_key'].nunique(), 'model': 'Cox PH; repository-clustered SE; adjusted for repo age'}
    exog = model_data[['post', 'log_repo_age']].astype(float)
    fit = PHReg(model_data['duration_days'].astype(float), exog, status=model_data['event'].astype(int), ties='efron').fit(groups=model_data['repo_key'])
    names = list(exog.columns)
    post_index = names.index('post')
    coefficient = float(fit.params[post_index])
    standard_error = float(fit.bse[post_index])
    residual = np.asarray(fit.schoenfeld_residuals)[:, post_index]
    event_mask = model_data['event'].to_numpy().astype(bool) & np.isfinite(residual)
    if event_mask.sum() >= 5:
        (_, ph_p) = stats.spearmanr(np.log(model_data.loc[event_mask, 'duration_days']), residual[event_mask])
    else:
        ph_p = np.nan
    return {'hazard_ratio': math.exp(coefficient), 'ci_low': math.exp(coefficient - 1.96 * standard_error), 'ci_high': math.exp(coefficient + 1.96 * standard_error), 'p': float(fit.pvalues[post_index]), 'ph_test_p': float(ph_p), 'n_items': len(model_data), 'n_events': int(model_data['event'].sum()), 'n_repos': model_data['repo_key'].nunique(), 'model': 'Cox PH; repository-clustered SE; adjusted for repo age'}

def survival_fixed_horizon_gee(group: pd.DataFrame, horizon_days: int=30) -> dict[str, float]:
    data = group.copy()
    data['available_followup'] = (data['administrative_censor_time'] - data['created_time']).dt.total_seconds() / 86400
    data = data[data['available_followup'].ge(horizon_days)].copy()
    data['event_within_horizon'] = (data['event'].eq(1) & data['duration_days'].le(horizon_days)).astype(int)
    if data.empty or data['post'].nunique() < 2:
        return {'n_items': len(data), 'n_repos': data['repo_key'].nunique(), 'pre_rate': np.nan, 'post_rate': np.nan, 'odds_ratio': np.nan, 'ci_low': np.nan, 'ci_high': np.nan, 'p': np.nan}
    rates = data.groupby('period')['event_within_horizon'].mean()
    exog = sm.add_constant(data[['post', 'log_repo_age']], has_constant='add')
    fit = sm.GEE(data['event_within_horizon'], exog, groups=data['repo_key'], family=Binomial(), cov_struct=Independence()).fit()
    coefficient = float(fit.params['post'])
    (low, high) = fit.conf_int().loc['post']
    return {'n_items': len(data), 'n_repos': data['repo_key'].nunique(), 'pre_rate': float(rates.get('pre', np.nan)), 'post_rate': float(rates.get('post', np.nan)), 'odds_ratio': math.exp(coefficient), 'ci_low': math.exp(float(low)), 'ci_high': math.exp(float(high)), 'p': float(fit.pvalues['post'])}

def survival_rank_biserial(pre: pd.Series, post: pd.Series) -> float:
    difference = (post - pre).dropna()
    difference = difference[difference.ne(0)]
    if difference.empty:
        return 0.0
    ranks = stats.rankdata(difference.abs())
    positive = ranks[difference.gt(0)].sum()
    negative = ranks[difference.lt(0)].sum()
    return float((positive - negative) / ranks.sum())

def survival_paired_fixed_horizon(group: pd.DataFrame, horizon_days: int=30) -> dict[str, float | int]:
    """Compare repository-level event rates with complete horizon follow-up."""
    data = group.copy()
    data['available_followup'] = (data['administrative_censor_time'] - data['created_time']).dt.total_seconds() / 86400
    data = data[data['available_followup'].ge(horizon_days)].copy()
    data['event_within_horizon'] = (data['event'].eq(1) & data['duration_days'].le(horizon_days)).astype(int)
    rates = data.groupby(['repo_key', 'period'], observed=True)['event_within_horizon'].mean().unstack('period').dropna(subset=['pre', 'post'])
    difference = rates['post'] - rates['pre']
    if rates.empty or not difference.ne(0).any():
        p_value = 1.0
    else:
        (_, p_value) = stats.wilcoxon(rates['post'], rates['pre'], zero_method='wilcox')
    return {'n_repositories': len(rates), 'pre_mean': float(rates['pre'].mean()), 'post_mean': float(rates['post'].mean()), 'pre_median': float(rates['pre'].median()), 'post_median': float(rates['post'].median()), 'mean_change': float(difference.mean()), 'p': float(p_value), 'rank_biserial_r': survival_rank_biserial(rates['pre'], rates['post'])}

def survival_plot_curves(curves: pd.DataFrame) -> None:
    labels = {'issue': 'Issue closure', 'pull_request': 'PR merge'}
    cohorts = ['main_608', 'older_114']
    (figure, axes) = plt.subplots(2, 2, figsize=(7.1, 5.2), sharex=True, sharey=True)
    colors = {'pre': '#3B6FB6', 'post': '#D66A3A'}
    for (row, cohort_name) in enumerate(cohorts):
        for (column, item_type) in enumerate(['issue', 'pull_request']):
            axis = axes[row, column]
            subset = curves[curves['cohort'].eq(cohort_name) & curves['item_type'].eq(item_type)]
            for period in ['pre', 'post']:
                curve = subset[subset['period'].eq(period)]
                if curve.empty:
                    continue
                axis.step(curve['time_days'], 1 - curve['survival'], where='post', color=colors[period], linewidth=1.5, label='Before' if period == 'pre' else 'After')
            axis.set_xlim(0, 365)
            axis.set_ylim(0, 1)
            axis.grid(axis='y', color='#dddddd', linewidth=0.6)
            axis.set_title(f"{labels[item_type]} ({('main' if row == 0 else 'validation')})", fontsize=9)
            axis.tick_params(labelsize=8)
            if row == 1:
                axis.set_xlabel('Days since opening', fontsize=8)
            if column == 0:
                axis.set_ylabel('Cumulative event probability', fontsize=8)
    (handles, legend_labels) = axes[0, 0].get_legend_handles_labels()
    figure.legend(handles, legend_labels, loc='upper center', ncol=2, frameon=False, fontsize=8)
    figure.tight_layout(rect=(0, 0, 1, 0.96))
    figure.savefig(survival_OUT / 'issue_pr_survival_curves.pdf', bbox_inches='tight')
    figure.savefig(survival_OUT / 'issue_pr_survival_curves.png', dpi=300, bbox_inches='tight')
    plt.close(figure)

def survival_main() -> None:
    metadata = pd.read_csv(survival_DATA / 'repo_metadata.csv', low_memory=False)
    metadata['repo_key'] = survival_repo_key(metadata['repo_full_name'])
    metadata['collection_end'] = survival_to_utc(metadata['collection_finished_at'])
    issues = pd.read_csv(survival_DATA / 'repo_issues_full.csv', low_memory=False)
    issues['repo_key'] = survival_repo_key(issues['repo_full_name'])
    issues = issues[~survival_as_bool(issues['is_pull_request'])].copy()
    issues['created_time'] = survival_to_utc(issues['created_at'])
    issues['closed_time'] = survival_to_utc(issues['closed_at'])
    issues = issues.drop_duplicates(['repo_key', 'issue_id'])
    pulls = pd.read_csv(survival_DATA / 'repo_pull_requests_full.csv', low_memory=False)
    pulls['repo_key'] = survival_repo_key(pulls['repo_full_name'])
    pulls['created_time'] = survival_to_utc(pulls['created_at'])
    pulls['closed_time'] = survival_to_utc(pulls['closed_at'])
    pulls['merged_time'] = survival_to_utc(pulls['merged_at'])
    pulls = pulls.drop_duplicates(['repo_key', 'pr_id'])
    panels: list[pd.DataFrame] = []
    km_curves: list[pd.DataFrame] = []
    summaries: list[dict[str, object]] = []
    cox_rows: list[dict[str, object]] = []
    horizon_rows: list[dict[str, object]] = []
    paired_horizon_rows: list[dict[str, object]] = []
    for (cohort_name, cohort_path) in survival_COHORTS.items():
        cohort = survival_prepare_cohort(cohort_path, metadata)
        for (item_type, items) in [('issue', issues), ('pull_request', pulls)]:
            panel = survival_build_item_panel(items, cohort, item_type)
            panel['cohort'] = cohort_name
            panels.append(panel)
            for (period, group) in panel.groupby('period', observed=True):
                curve = survival_kaplan_meier(group)
                curve['cohort'] = cohort_name
                curve['item_type'] = item_type
                curve['period'] = period
                km_curves.append(curve)
                summaries.append({'cohort': cohort_name, 'item_type': item_type, 'period': period, 'n_items': len(group), 'n_repositories': group['repo_key'].nunique(), 'events': int(group['event'].sum()), 'censored': int(group['event'].eq(0).sum()), 'observed_event_percent': 100 * group['event'].mean(), 'km_event_by_30d_percent': 100 * (1 - survival_km_value(curve, 30)), 'km_event_by_90d_percent': 100 * (1 - survival_km_value(curve, 90)), 'km_median_event_days': survival_km_median(curve)})
            cox = survival_fit_cox(panel)
            cox.update({'cohort': cohort_name, 'item_type': item_type})
            cox_rows.append(cox)
            horizon = survival_fixed_horizon_gee(panel, 30)
            horizon.update({'cohort': cohort_name, 'item_type': item_type, 'horizon_days': 30})
            horizon_rows.append(horizon)
            paired_horizon = survival_paired_fixed_horizon(panel, 30)
            paired_horizon.update({'cohort': cohort_name, 'item_type': item_type, 'horizon_days': 30})
            paired_horizon_rows.append(paired_horizon)
    item_panel = pd.concat(panels, ignore_index=True)
    curves = pd.concat(km_curves, ignore_index=True)
    summary = pd.DataFrame(summaries)
    cox_results = pd.DataFrame(cox_rows)
    horizon_results = pd.DataFrame(horizon_rows)
    paired_horizon_results = pd.DataFrame(paired_horizon_rows)
    cox_results['q'] = multipletests(cox_results['p'].fillna(1), method='fdr_bh')[1]
    horizon_results['q'] = multipletests(horizon_results['p'].fillna(1), method='fdr_bh')[1]
    paired_horizon_results['q'] = multipletests(paired_horizon_results['p'].fillna(1), method='fdr_bh')[1]
    export_columns = ['cohort', 'repo_key', 'item_type', 'item_id', 'created_time', 'relative_month', 'period', 'event', 'duration_days', 'followup_end', 'administrative_censor_time', 'repo_age_days_at_open']
    item_panel[export_columns].to_csv(survival_OUT / 'item_level_survival_panel.csv', index=False)
    curves.to_csv(survival_OUT / 'kaplan_meier_curves.csv', index=False)
    summary.to_csv(survival_OUT / 'kaplan_meier_summary.csv', index=False)
    cox_results.to_csv(survival_OUT / 'clustered_cox_results.csv', index=False)
    horizon_results.to_csv(survival_OUT / 'fixed_30day_sensitivity.csv', index=False)
    paired_horizon_results.to_csv(survival_OUT / 'paired_repository_30day_results.csv', index=False)
    survival_plot_curves(curves)
    print('Kaplan-Meier summaries')
    print(summary.to_string(index=False))
    print('\nClustered Cox models')
    print(cox_results.to_string(index=False))
    print('\nItem-weighted 30-day sensitivity')
    print(horizon_results.to_string(index=False))
    print('\nRepository-balanced 30-day comparison')
    print(paired_horizon_results.to_string(index=False))


# RQ2 COLLABORATION MONTHLY RATES
import math
import re
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats
import statsmodels.formula.api as smf
from statsmodels.stats.multitest import multipletests
collaboration_ROOT = Path(__file__).resolve().parents[1]
collaboration_DATA = collaboration_ROOT / 'data_complete' / 'repo_activity'
collaboration_COHORT_DIR = collaboration_ROOT / 'outputs' / 'toy_homework_audit'
collaboration_OUT = collaboration_ROOT / 'outputs' / 'rq_collaboration_review_filtered_1240' / 'monthly_recalc'
collaboration_COHORTS = {'main_608': collaboration_COHORT_DIR / 'main_comparison_chat_after_start_excluding_toy_homework_608.csv', 'older_114': collaboration_COHORT_DIR / 'older_pre2025_excluding_toy_homework_114.csv'}
collaboration_PARTICIPATION = ['active_contributors', 'unique_issue_commenters', 'unique_pr_reviewers']
collaboration_CONCENTRATION = ['top_contributor_share', 'contributor_hhi', 'top_commenter_share', 'commenter_hhi', 'reviewer_hhi']
collaboration_BOT_RE = re.compile('\\[bot\\]$|dependabot|renovate|github-actions|lovable-dev|google-labs-jules|devin-ai|(?:^|[^a-z])copilot(?:[^a-z]|$)|claude\\[bot\\]|v0\\[bot\\]', re.I)

def collaboration_to_utc(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, utc=True, errors='coerce', format='mixed')

def collaboration_repo_key(values: pd.Series) -> pd.Series:
    return values.astype(str).str.strip().str.lower()

def collaboration_as_bool(values: pd.Series) -> pd.Series:
    return values.astype('string').str.strip().str.lower().map({'true': True, 'false': False, '1': True, '0': False}).fillna(False).astype(bool)

def collaboration_commit_actor(frame: pd.DataFrame) -> pd.Series:
    login = frame['commit_author_login'].astype('string').str.strip().str.lower()
    email = frame['commit_author_email_hash'].astype('string').str.strip()
    name = frame['commit_author_name'].astype('string').str.strip().str.lower()
    actor = 'login:' + login
    actor = actor.mask(login.isna() | login.eq(''), 'email:' + email)
    actor = actor.mask((login.isna() | login.eq('')) & (email.isna() | email.eq('')), 'name:' + name)
    return actor

def collaboration_human_commit_mask(frame: pd.DataFrame) -> pd.Series:
    login = frame['commit_author_login'].fillna('').astype(str)
    name = frame['commit_author_name'].fillna('').astype(str)
    return ~(login.str.contains(collaboration_BOT_RE) | name.str.contains(collaboration_BOT_RE))

def collaboration_build_observation_grid(cohort: pd.DataFrame, cohort_name: str) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for row in cohort.itertuples(index=False):
        if pd.isna(row.created_at) or pd.isna(row.adoption_time) or pd.isna(row.collection_end):
            continue
        start = row.created_at.tz_convert(None).to_period('M')
        end = row.collection_end.tz_convert(None).to_period('M')
        adoption_month = row.adoption_time.tz_convert(None).to_period('M')
        for month in pd.period_range(start=start, end=end, freq='M'):
            relative_month = month.ordinal - adoption_month.ordinal
            month_start = month.start_time.tz_localize('UTC')
            rows.append({'cohort': cohort_name, 'repo_key': row.repo_key, 'calendar_month': str(month), 'relative_month': relative_month, 'period': 'pre' if relative_month < 0 else 'post' if relative_month > 0 else 'adoption', 'repo_age_months': max(0.0, (month_start - row.created_at).total_seconds() / (86400 * 30.4375))})
    grid = pd.DataFrame(rows)
    if grid.duplicated(['cohort', 'repo_key', 'calendar_month']).any():
        raise ValueError('Observation grid contains duplicate repository-months')
    return grid

def collaboration_monthly_actor_metrics(frame: pd.DataFrame, actor_column: str, event_column: str, count_name: str, top_name: str | None, hhi_name: str | None) -> pd.DataFrame:
    counts = frame.groupby(['repo_key', 'calendar_month', actor_column], as_index=False)[event_column].nunique().rename(columns={event_column: 'actor_events'})
    if counts.empty:
        columns = ['repo_key', 'calendar_month', count_name]
        if top_name:
            columns.append(top_name)
        if hhi_name:
            columns.append(hhi_name)
        return pd.DataFrame(columns=columns)
    counts['monthly_events'] = counts.groupby(['repo_key', 'calendar_month'])['actor_events'].transform('sum')
    counts['share'] = counts['actor_events'] / counts['monthly_events']
    aggregations: dict[str, tuple[str, object]] = {count_name: (actor_column, 'nunique')}
    if top_name:
        aggregations[top_name] = ('share', 'max')
    if hhi_name:
        aggregations[hhi_name] = ('share', lambda values: float(np.square(values).sum()))
    return counts.groupby(['repo_key', 'calendar_month'], as_index=False).agg(**aggregations)

def collaboration_rank_biserial(pre: pd.Series, post: pd.Series) -> float:
    difference = (post - pre).dropna()
    difference = difference[difference.ne(0)]
    if difference.empty:
        return 0.0
    ranks = stats.rankdata(difference.abs())
    return float((ranks[difference.gt(0)].sum() - ranks[difference.lt(0)].sum()) / ranks.sum())

def collaboration_paired_tests(panel: pd.DataFrame, cohort_name: str) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for (family, measures) in [('participation', collaboration_PARTICIPATION), ('concentration', collaboration_CONCENTRATION)]:
        family_rows = []
        for measure in measures:
            values = panel.groupby(['repo_key', 'period'])[measure].mean().unstack('period').dropna(subset=['pre', 'post'])
            difference = values['post'] - values['pre']
            p_value = stats.wilcoxon(values['post'], values['pre'], zero_method='wilcox').pvalue if difference.ne(0).any() else 1.0
            family_rows.append({'cohort': cohort_name, 'family': family, 'measure': measure, 'n': len(values), 'pre_mean': values['pre'].mean(), 'post_mean': values['post'].mean(), 'pre_median': values['pre'].median(), 'post_median': values['post'].median(), 'mean_change': difference.mean(), 'median_change': difference.median(), 'pct_increasing': difference.gt(0).mean() * 100, 'pct_decreasing': difference.lt(0).mean() * 100, 'pct_unchanged': difference.eq(0).mean() * 100, 'p': p_value, 'rank_biserial_r': collaboration_rank_biserial(values['pre'], values['post'])})
        family_result = pd.DataFrame(family_rows)
        valid = family_result['p'].notna()
        family_result.loc[valid, 'q'] = multipletests(family_result.loc[valid, 'p'], method='fdr_bh')[1]
        rows.extend(family_result.to_dict('records'))
    return pd.DataFrame(rows)

def collaboration_fit_participation_its(panel: pd.DataFrame, cohort_name: str) -> pd.DataFrame:
    data = panel.copy()
    data['pretime'] = np.where(data['relative_month'].lt(0), data['relative_month'], 0)
    data['post'] = data['relative_month'].gt(0).astype(int)
    data['posttime'] = np.where(data['relative_month'].gt(0), data['relative_month'], 0)
    data['log_age'] = np.log1p(data['repo_age_months'].clip(lower=0))
    rows: list[dict[str, object]] = []
    for measure in collaboration_PARTICIPATION:
        data['outcome'] = np.log1p(data[measure])
        model = smf.ols('outcome ~ pretime + post + posttime + log_age + C(repo_key)', data=data).fit(cov_type='cluster', cov_kwds={'groups': data['repo_key']})
        for term in ['pretime', 'post', 'posttime']:
            coefficient = float(model.params[term])
            rows.append({'cohort': cohort_name, 'measure': measure, 'term': term, 'coef': coefficient, 'effect_percent': 100 * math.expm1(coefficient), 'p': float(model.pvalues[term]), 'n_obs': int(model.nobs), 'n_repos': data['repo_key'].nunique()})
        names = list(model.params.index)
        contrast = np.zeros(len(names))
        contrast[names.index('posttime')] = 1
        contrast[names.index('pretime')] = -1
        test = model.t_test(contrast)
        coefficient = float(test.effect[0])
        rows.append({'cohort': cohort_name, 'measure': measure, 'term': 'posttime_minus_pretime', 'coef': coefficient, 'effect_percent': 100 * math.expm1(coefficient), 'p': float(test.pvalue), 'n_obs': int(model.nobs), 'n_repos': data['repo_key'].nunique()})
    result = pd.DataFrame(rows)
    result['q'] = multipletests(result['p'], method='fdr_bh')[1]
    return result

def collaboration_main() -> None:
    metadata = pd.read_csv(collaboration_DATA / 'repo_metadata.csv')
    metadata['repo_key'] = collaboration_repo_key(metadata['repo_full_name'])
    metadata['collection_end'] = collaboration_to_utc(metadata['collection_finished_at'])
    fallback_end = metadata['collection_end'].max()
    commits = pd.read_csv(collaboration_DATA / 'repo_commits_with_churn_full.csv', low_memory=False)
    comments = pd.read_csv(collaboration_DATA / 'repo_comments_full.csv', low_memory=False)
    reviews = pd.read_csv(collaboration_DATA / 'repo_pr_reviews_full.csv', low_memory=False)
    for frame in [commits, comments, reviews]:
        frame['repo_key'] = collaboration_repo_key(frame['repo_full_name'])
    commits['event_time'] = collaboration_to_utc(commits['commit_timestamp'])
    comments['event_time'] = collaboration_to_utc(comments['created_at'])
    reviews['event_time'] = collaboration_to_utc(reviews['created_at'])
    for frame in [commits, comments, reviews]:
        frame['calendar_month'] = frame['event_time'].dt.tz_convert(None).dt.to_period('M').astype(str)
    commits['actor'] = collaboration_commit_actor(commits)
    commits = commits[~collaboration_as_bool(commits['is_chat_history_only_commit']) & collaboration_human_commit_mask(commits) & commits['actor'].notna()].copy()
    issue_comments = comments[comments['comment_type'].eq('issue_comment') & ~comments['user_login'].fillna('').astype(str).str.contains(collaboration_BOT_RE) & comments['user_login'].notna()].copy()
    issue_comments['actor'] = 'login:' + issue_comments['user_login'].astype(str).str.lower()
    human_reviews = reviews[~reviews['user_login'].fillna('').astype(str).str.contains(collaboration_BOT_RE) & reviews['user_login'].notna()].copy()
    human_reviews['actor'] = 'login:' + human_reviews['user_login'].astype(str).str.lower()
    activity_tables = [collaboration_monthly_actor_metrics(commits, 'actor', 'commit_sha', 'active_contributors', 'top_contributor_share', 'contributor_hhi'), collaboration_monthly_actor_metrics(issue_comments, 'actor', 'comment_id', 'unique_issue_commenters', 'top_commenter_share', 'commenter_hhi'), collaboration_monthly_actor_metrics(human_reviews, 'actor', 'review_id', 'unique_pr_reviewers', None, 'reviewer_hhi')]
    panels = []
    tests = []
    models = []
    coverage_rows = []
    for (cohort_name, cohort_path) in collaboration_COHORTS.items():
        cohort = pd.read_csv(cohort_path)
        cohort['repo_key'] = collaboration_repo_key(cohort['repo_full_name'])
        cohort['created_at'] = collaboration_to_utc(cohort['created_at'])
        cohort['adoption_time'] = collaboration_to_utc(cohort['first_ai_session_time'])
        cohort = cohort.merge(metadata[['repo_key', 'collection_end']], on='repo_key', how='left')
        cohort['collection_end'] = cohort['collection_end'].fillna(fallback_end)
        grid = collaboration_build_observation_grid(cohort, cohort_name)
        for activity in activity_tables:
            grid = grid.merge(activity, on=['repo_key', 'calendar_month'], how='left')
        for measure in collaboration_PARTICIPATION:
            grid[measure] = grid[measure].fillna(0).astype(int)
        inference = grid[grid['relative_month'].ne(0)].copy()
        side_count = inference.groupby('repo_key')['period'].nunique()
        eligible = side_count[side_count.eq(2)].index
        inference = inference[inference['repo_key'].isin(eligible)].copy()
        panels.append(grid.assign(eligible_paired=grid['repo_key'].isin(eligible)))
        tests.append(collaboration_paired_tests(inference, cohort_name))
        models.append(collaboration_fit_participation_its(inference, cohort_name))
        coverage_rows.append({'cohort': cohort_name, 'cohort_repositories': cohort['repo_key'].nunique(), 'paired_repositories': len(eligible), 'inference_repository_months': len(inference), 'zero_month_share_active_contributors': inference['active_contributors'].eq(0).mean(), 'zero_month_share_unique_issue_commenters': inference['unique_issue_commenters'].eq(0).mean(), 'zero_month_share_unique_pr_reviewers': inference['unique_pr_reviewers'].eq(0).mean()})
    pd.concat(panels, ignore_index=True).to_csv(collaboration_OUT / 'relative_month_collaboration_panel_zero_filled_608_114.csv', index=False)
    pd.concat(tests, ignore_index=True).to_csv(collaboration_OUT / 'repository_monthly_participation_concentration_tests_608_114.csv', index=False)
    pd.concat(models, ignore_index=True).to_csv(collaboration_OUT / 'relative_month_participation_its_models_608_114.csv', index=False)
    pd.DataFrame(coverage_rows).to_csv(collaboration_OUT / 'panel_coverage_608_114.csv', index=False)


# RQ2 ITS SENSITIVITY
import math
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.multitest import multipletests
sensitivity_ROOT = Path(__file__).resolve().parents[1]
sensitivity_OUT = sensitivity_ROOT / 'outputs' / 'rq2_its_sensitivity'
sensitivity_WINDOWS = (3, 6, 12)
sensitivity_EXCLUSION_RADII = (0, 1)
sensitivity_TERMS = ('pretime', 'post', 'posttime', 'posttime_minus_pretime')

def sensitivity_normalized_repo(values: pd.Series) -> pd.Series:
    return values.astype(str).str.strip().str.lower()

def sensitivity_add_age_from_cohort(panel: pd.DataFrame, cohort_file: Path) -> pd.DataFrame:
    cohort = pd.read_csv(cohort_file)
    cohort['repo_key'] = sensitivity_normalized_repo(cohort['repo_full_name'])
    created = pd.to_datetime(cohort['created_at'], utc=True, errors='coerce')
    adopted = pd.to_datetime(cohort['first_ai_session_time'], utc=True, errors='coerce')
    cohort['age_at_adoption_months'] = ((adopted - created).dt.total_seconds() / (86400 * 30.4375)).clip(lower=0)
    result = panel.copy()
    result['repo_key'] = sensitivity_normalized_repo(result['repo_key'])
    result = result.merge(cohort[['repo_key', 'age_at_adoption_months']], on='repo_key', how='left', validate='many_to_one')
    result['repo_age_months'] = (result['age_at_adoption_months'] + result['relative_month']).clip(lower=0)
    return result.drop(columns='age_at_adoption_months')

def sensitivity_prepare_inputs() -> list[dict[str, object]]:
    inputs: list[dict[str, object]] = []
    commit = pd.read_csv(sensitivity_ROOT / 'outputs' / 'relative_month_commit_activity_filtered_1240' / 'relative_month_commit_panel_filtered.csv')
    inputs.append({'family': 'commit_activity', 'panel': commit, 'outcomes': {'total_commits': 'log1p', 'substantive_commits': 'log1p', 'source_code_commits': 'log1p'}})
    issue_pr = pd.read_csv(SUPP_OUT / 'issue_pr_monthly' / 'relative_month_issue_pr_panel_zero_filled_608_114.csv')
    inputs.append({'family': 'issue_pr_activity', 'panel': issue_pr, 'outcomes': {'issues_opened': 'log1p', 'issues_closed': 'log1p', 'prs_opened': 'log1p', 'prs_merged': 'log1p'}})
    collaboration = pd.read_csv(SUPP_OUT / 'collaboration_monthly' / 'relative_month_collaboration_panel_zero_filled_608_114.csv')
    inputs.append({'family': 'collaboration', 'panel': collaboration, 'outcomes': {'active_contributors': 'log1p', 'unique_issue_commenters': 'log1p', 'unique_pr_reviewers': 'log1p'}})
    quality_dir = sensitivity_ROOT / 'outputs' / 'rq_quality_maintenance_monthly' / 'tables'
    cohort_dir = sensitivity_ROOT / 'outputs' / 'toy_homework_audit'
    quality_panels = []
    for (cohort, panel_name, cohort_name) in [('main_608', 'main_repository_month_panel.csv', 'main_comparison_chat_after_start_excluding_toy_homework_608.csv'), ('older_114', 'older_repository_month_panel.csv', 'older_pre2025_excluding_toy_homework_114.csv')]:
        panel = pd.read_csv(quality_dir / panel_name)
        panel['cohort'] = cohort
        quality_panels.append(sensitivity_add_age_from_cohort(panel, cohort_dir / cohort_name))
    quality = pd.concat(quality_panels, ignore_index=True)
    inputs.append({'family': 'quality_maintenance', 'panel': quality, 'outcomes': {'bug_fix_commits': 'log1p', 'bug_fix_commit_share': 'identity', 'bug_issues_opened': 'log1p', 'test_commit_share': 'identity', 'ci_failure_rate': 'identity', 'ci_success_rate': 'identity', 'source_churn_per_source_commit': 'log1p', 'files_changed_per_commit': 'log1p', 'same_file_rework_7d_share': 'identity', 'same_file_rework_30d_share': 'identity', 'same_file_bug_fix_followup_30d_share': 'identity', 'revert_commit_share': 'identity'}})
    return inputs

def sensitivity_within_cluster_fit(frame: pd.DataFrame, outcome: str, transform: str) -> list[dict[str, float]]:
    columns = ['repo_key', 'relative_month', 'repo_age_months', outcome]
    data = frame[columns].dropna().copy()
    data['pretime'] = np.where(data['relative_month'].lt(0), data['relative_month'], 0.0)
    data['post'] = data['relative_month'].gt(0).astype(float)
    data['posttime'] = np.where(data['relative_month'].gt(0), data['relative_month'], 0.0)
    data['log_age'] = np.log1p(data['repo_age_months'].clip(lower=0))
    if transform == 'log1p':
        data['y'] = np.log1p(data[outcome].clip(lower=0))
    else:
        data['y'] = data[outcome].astype(float)
    side = data.groupby('repo_key')['relative_month'].agg(has_pre=lambda x: x.lt(0).any(), has_post=lambda x: x.gt(0).any())
    eligible = side.index[side['has_pre'] & side['has_post']]
    data = data[data['repo_key'].isin(eligible)].copy()
    if len(eligible) < 2:
        return []
    predictors = ['pretime', 'post', 'posttime', 'log_age']
    means = data.groupby('repo_key')[['y', *predictors]].transform('mean')
    demeaned = data[['y', *predictors]] - means
    y = demeaned['y'].to_numpy(dtype=float)
    x = demeaned[predictors].to_numpy(dtype=float)
    keep = np.linalg.norm(x, axis=0) > 1e-12
    if not keep.all():
        return []
    xtx_inv = np.linalg.pinv(x.T @ x)
    beta = xtx_inv @ x.T @ y
    residual = y - x @ beta
    groups = data['repo_key'].to_numpy()
    meat = np.zeros((x.shape[1], x.shape[1]))
    for group in pd.unique(groups):
        idx = groups == group
        score = x[idx].T @ residual[idx]
        meat += np.outer(score, score)
    n_obs = len(data)
    n_repos = len(eligible)
    k_full = n_repos + len(predictors)
    correction = n_repos / (n_repos - 1) * ((n_obs - 1) / max(n_obs - k_full, 1))
    covariance = correction * xtx_inv @ meat @ xtx_inv
    rows = []
    contrast_vectors = {'pretime': np.array([1.0, 0.0, 0.0, 0.0]), 'post': np.array([0.0, 1.0, 0.0, 0.0]), 'posttime': np.array([0.0, 0.0, 1.0, 0.0]), 'posttime_minus_pretime': np.array([-1.0, 0.0, 1.0, 0.0])}
    for (term, contrast) in contrast_vectors.items():
        estimate = float(contrast @ beta)
        variance = float(contrast @ covariance @ contrast)
        standard_error = math.sqrt(max(variance, 0.0))
        statistic = estimate / standard_error if standard_error > 0 else np.nan
        p_value = 2 * stats.norm.sf(abs(statistic)) if np.isfinite(statistic) else np.nan
        effect = 100 * math.expm1(estimate) if transform == 'log1p' else 100 * estimate
        rows.append({'term': term, 'coef': estimate, 'effect': effect, 'effect_unit': 'percent' if transform == 'log1p' else 'percentage_points', 'std_error': standard_error, 'p': p_value, 'n_obs': n_obs, 'n_repos': n_repos})
    return rows

def sensitivity_run_grid() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for specification in sensitivity_prepare_inputs():
        family = str(specification['family'])
        panel = specification['panel'].copy()
        panel['repo_key'] = sensitivity_normalized_repo(panel['repo_key'])
        if 'repo_age_months' not in panel:
            raise ValueError(f'{family} panel lacks repo_age_months')
        for cohort in ('main_608', 'older_114'):
            cohort_panel = panel[panel['cohort'].eq(cohort)].copy()
            for window in sensitivity_WINDOWS:
                for radius in sensitivity_EXCLUSION_RADII:
                    selected = cohort_panel[cohort_panel['relative_month'].between(-window, window) & cohort_panel['relative_month'].abs().gt(radius)].copy()
                    for (outcome, transform) in specification['outcomes'].items():
                        if outcome not in selected:
                            continue
                        estimates = sensitivity_within_cluster_fit(selected, outcome, transform)
                        for estimate in estimates:
                            rows.append({'family': family, 'cohort': cohort, 'window_months': window, 'transition_exclusion': 'month_0' if radius == 0 else 'months_-1_to_+1', 'exclusion_radius': radius, 'outcome': outcome, 'transform': transform, **estimate})
    result = pd.DataFrame(rows)
    result['q'] = np.nan
    for ((_, window, radius), index) in result.groupby(['family', 'window_months', 'exclusion_radius']).groups.items():
        valid = result.loc[index, 'p'].notna()
        valid_index = result.loc[index].index[valid]
        if len(valid_index):
            result.loc[valid_index, 'q'] = multipletests(result.loc[valid_index, 'p'], method='fdr_bh')[1]
    return result

def sensitivity_summarize_stability(results: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (keys, group) in results.groupby(['family', 'cohort', 'outcome', 'term']):
        nonzero = group['coef'].ne(0)
        dominant_sign = np.sign(group.loc[nonzero, 'coef']).mode()
        expected_sign = int(dominant_sign.iloc[0]) if len(dominant_sign) else 0
        rows.append({'family': keys[0], 'cohort': keys[1], 'outcome': keys[2], 'term': keys[3], 'specifications': len(group), 'negative_specifications': int(group['coef'].lt(0).sum()), 'positive_specifications': int(group['coef'].gt(0).sum()), 'dominant_direction': 'negative' if expected_sign < 0 else 'positive', 'direction_consistency_pct': 100 * np.sign(group['coef']).eq(expected_sign).mean(), 'fdr_significant_specifications': int(group['q'].lt(0.05).sum()), 'min_q': group['q'].min(), 'max_q': group['q'].max(), 'effect_min': group['effect'].min(), 'effect_max': group['effect'].max(), 'min_repositories': int(group['n_repos'].min()), 'max_repositories': int(group['n_repos'].max())})
    return pd.DataFrame(rows)

def sensitivity_main() -> None:
    results = sensitivity_run_grid()
    stability = sensitivity_summarize_stability(results)
    results.to_csv(sensitivity_OUT / 'its_sensitivity_all_estimates.csv', index=False)
    stability.to_csv(sensitivity_OUT / 'its_sensitivity_stability_summary.csv', index=False)
    print(results.groupby(['family', 'cohort']).size())
    print(f'Saved {len(results):,} estimates to {sensitivity_OUT}')


def configure_updated_rq2():
    """Use the requested root and keep all generated outputs under SUPP_OUT."""
    cohorts = {"main_608": FILTERED_MAIN, "older_114": FILTERED_OLD}
    for prefix, folder in [("issue_monthly_", "issue_pr_monthly"), ("survival_", "issue_pr_survival"), ("collaboration_", "collaboration_monthly"), ("sensitivity_", "its_sensitivity")]:
        globals()[prefix + "ROOT"] = ROOT
        globals()[prefix + "DATA"] = REPO_ACTIVITY
        globals()[prefix + "COHORT_DIR"] = COHORT_DIR
        globals()[prefix + "COHORTS"] = cohorts
        globals()[prefix + "OUT"] = SUPP_OUT / folder
        ensure_dir(SUPP_OUT / folder)


def run_temporal_sensitivity():
    """Re-estimate the exact six temporal configurations on derived panels.

    Use the study's original commit/quality panels (including the complete
    correction family), replacing issue/PR and collaboration paths with the
    outputs generated by this consolidated script.
    """
    configure_updated_rq2()
    result = sensitivity_run_grid()
    result.to_csv(sensitivity_OUT / "its_sensitivity_all_estimates.csv", index=False)
    sensitivity_summarize_stability(result).to_csv(sensitivity_OUT / "its_sensitivity_stability_summary.csv", index=False)


def show_chat_samples():
    """List the 100 Markdown histories without requiring the full dataset."""
    from supplementary_data_collection import parse_standard_specstory_messages
    folder = Path(__file__).resolve().parent / "data"
    rows = []
    for path in sorted(folder.glob("chat_sample_*.md")):
        messages = parse_standard_specstory_messages(path.read_text())
        rows.append({"sample_id": path.stem, "messages": messages})
    print(json.dumps([
        {"file": row["sample_id"] + ".md", "messages": len(row["messages"])}
        for row in rows
    ], ensure_ascii=False, indent=2))
    return rows



def main() -> None:
    global ROOT, CLEAN, REPO_ACTIVITY, OUTPUTS, SUPP_OUT, COHORT_DIR, FILTERED_ALL, FILTERED_MAIN, FILTERED_OLD
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--survey", type=Path, default=ROOT / "vibe-coding_survey.csv")
    parser.add_argument("--samples", action="store_true", help="Display included chat samples; no full data required")
    parser.add_argument("--sensitivity", action="store_true", help="Also run ITS window/exclusion checks; requires the study's derived commit and quality panels")
    args = parser.parse_args()
    if args.samples:
        show_chat_samples()
        return

    ROOT = args.root.resolve()
    CLEAN = ROOT / "clean_data"
    REPO_ACTIVITY = ROOT / "data_complete" / "repo_activity"
    OUTPUTS = ROOT / "outputs"
    SUPP_OUT = OUTPUTS / "supplementary_reproducible_analysis"
    COHORT_DIR = OUTPUTS / "toy_homework_audit"
    FILTERED_ALL = COHORT_DIR / "complete_history_repos_excluding_strict_name_toy_homework_1240.csv"
    FILTERED_MAIN = COHORT_DIR / "main_comparison_chat_after_start_excluding_toy_homework_608.csv"
    FILTERED_OLD = COHORT_DIR / "older_pre2025_excluding_toy_homework_114.csv"
    ensure_dir(SUPP_OUT)

    all_repos, main_repos, old_repos = load_canonical_cohorts()
    tables = load_clean_tables()
    required = {"sessions", "commits", "commit_files"}
    missing = required - set(tables)
    if missing:
        raise SystemExit(f"Missing required clean tables: {sorted(missing)}")

    rq1_ai_use(tables, all_repos)
    rq1_chat_purpose(tables, all_repos)
    rq2_commit_activity_and_quality(tables, main_repos, old_repos)
    rq2_issues_prs(tables, main_repos, old_repos)
    rq2_collaboration_review(tables, main_repos, old_repos)
    rq3_survey(args.survey)
    if args.sensitivity:
        run_temporal_sensitivity()


if __name__ == "__main__":
    main()
