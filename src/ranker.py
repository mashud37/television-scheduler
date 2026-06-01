from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import joblib
import numpy as np
import pandas as pd
import yaml

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLED_MODEL = os.path.join(_REPO_ROOT, "assets", "models", "tv_component_models.joblib")
MODEL_PATH = os.environ.get("MODEL_PATH", "/mnt/data/models/tv_component_models.joblib")
# Preferred path: YAML (hand-editable). RANKER_PREFS_PATH wins if set; otherwise
# CONFIG_PATH is honoured for backwards compatibility — extension decides the
# parser. The /mnt/data default mirrors the existing GCS-mount layout.
RANKER_PREFS_PATH = os.environ.get(
    "RANKER_PREFS_PATH",
    os.environ.get("CONFIG_PATH", "/mnt/data/ranker_prefs.yaml"),
)
# Bundled example, used if the user's prefs file is absent — keeps a fresh
# install functional without per-user editing required.
BUNDLED_PREFS_EXAMPLE = os.path.join(_REPO_ROOT, "ranker_prefs.example.yaml")


@dataclass
class RankerConfig:
    """User-tunable preferences for the show ranker.

    All personal data lives OUTSIDE this dataclass — the defaults here are
    intentionally generic / empty so a fresh deploy ships with no embedded
    user preferences. Hand-editable overrides live in ranker_prefs.yaml
    (path: RANKER_PREFS_PATH); see ranker_prefs.example.yaml for the
    schema and a worked example.
    """
    # Empty by default — user fills in channel weights in their YAML.
    channel_prior: Dict[str, float] = field(default_factory=dict)
    default_channel_score: float = 1.0
    # Empty by default — user fills in their must-watch shows in YAML.
    must_watch_keywords: List[str] = field(default_factory=list)
    # Generic component weights — app-tuning, not user-personal.
    component_weights: Dict[str, float] = field(default_factory=lambda: {
        "channel": 1.5, "title": 1.3, "Description": 0.7, "Cast": 0.25, "Crew": 0.15,
    })
    # Generic prime-time slot bounds (20:00 / 22:00 / 24:00). Override in YAML
    # to match your viewing pattern.
    early_start_min: int = 20 * 60
    late_start_min: int = 22 * 60
    late_end_min: int = 24 * 60


def _read_prefs_file(path: str) -> dict:
    """Parse a prefs file. YAML if path ends .yaml/.yml or content starts non-
    JSON-ish; JSON otherwise. Returns {} on any read/parse failure (caller
    decides what to do with empty config)."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        if path.lower().endswith((".yaml", ".yml")):
            data = yaml.safe_load(text)
        elif path.lower().endswith(".json"):
            data = json.loads(text)
        else:
            # Unknown extension: try YAML (a superset of JSON for our purposes).
            data = yaml.safe_load(text)
        return data or {}
    except Exception as e:
        print(f"Prefs load failed ({path}): {e}")
        return {}


def load_config() -> RankerConfig:
    """Load ranker preferences. Order:
      1. RANKER_PREFS_PATH if it exists.
      2. CONFIG_PATH if it exists (backwards compat — may be JSON).
      3. Bundled ranker_prefs.example.yaml so a fresh deploy isn't empty.
      4. Neutral defaults (empty channel_prior + must_watch).
    """
    cfg = RankerConfig()
    data = _read_prefs_file(RANKER_PREFS_PATH)
    if not data:
        data = _read_prefs_file(BUNDLED_PREFS_EXAMPLE)
        if data:
            print(f"Using bundled example prefs at {BUNDLED_PREFS_EXAMPLE} "
                  "(no user prefs file found)")
    for k, v in data.items():
        if hasattr(cfg, k) and v is not None:
            setattr(cfg, k, v)
    return cfg


def save_config(cfg: RankerConfig, path: Optional[str] = None) -> None:
    """Write the prefs to YAML. Atomic write: temp file + rename."""
    target = path or RANKER_PREFS_PATH
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    payload = {
        "channel_prior": dict(cfg.channel_prior),
        "default_channel_score": cfg.default_channel_score,
        "must_watch_keywords": list(cfg.must_watch_keywords),
        "component_weights": dict(cfg.component_weights),
        "early_start_min": cfg.early_start_min,
        "late_start_min": cfg.late_start_min,
        "late_end_min": cfg.late_end_min,
    }
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False, allow_unicode=True)
    os.replace(tmp, target)


def load_model() -> Optional[dict]:
    for path in [MODEL_PATH, BUNDLED_MODEL]:
        if os.path.exists(path):
            try:
                m = joblib.load(path)
                print(f"Loaded model from {path}")
                return m
            except Exception as e:
                print(f"Failed to load model from {path}: {e}")
    print("No model available — ranking by channel/keywords only")
    return None


def _parse_start_min(time_str: str) -> float:
    if not isinstance(time_str, str):
        return np.nan
    m = re.search(r"(\d{1,2}):(\d{2})", time_str.replace(".", ":"))
    if not m:
        return np.nan
    return int(m.group(1)) * 60 + int(m.group(2))


def _slot(mins: float, early: int, late: int, end: int) -> str:
    if pd.isna(mins):
        return "night"
    if early <= mins < late:
        return "early"
    if late <= mins < end:
        return "late"
    return "night"


def _zscore(s: pd.Series) -> pd.Series:
    mu, sd = s.mean(), s.std(ddof=0)
    if sd == 0 or not np.isfinite(sd):
        return s * 0.0
    return (s - mu) / sd


def _must_watch_re(keywords: List[str]) -> Optional[re.Pattern]:
    kws = [k.strip() for k in keywords if k and k.strip()]
    if not kws:
        return None
    return re.compile(
        "|".join(rf"\b{re.escape(k)}\b" for k in kws),
        flags=re.IGNORECASE,
    )


def score_shows(shows: list, model: Optional[dict], config: RankerConfig) -> list:
    if not shows:
        return shows

    df = pd.DataFrame(shows)
    for col in ["channel", "time", "title", "date", "weekday", "Rating", "Description", "Cast", "Crew"]:
        if col not in df.columns:
            df[col] = ""

    df["start_min"] = df["time"].apply(_parse_start_min)
    df["slot"] = df["start_min"].apply(
        lambda x: _slot(x, config.early_start_min, config.late_start_min, config.late_end_min)
    )
    df["s_channel_raw"] = (
        df["channel"].map(config.channel_prior).fillna(config.default_channel_score).astype(float)
    )

    has_text = model is not None
    text_fields = []
    if has_text:
        meta = model.get("meta", {})
        text_fields = meta.get("text_fields", ["title", "Description", "Cast", "Crew"])
        for tf in text_fields:
            if tf in model:
                df[f"s_{tf}_raw"] = model[tf].predict(df[tf].fillna("").astype(str))
            else:
                df[f"s_{tf}_raw"] = 0.0
    else:
        text_fields = ["title", "Description", "Cast", "Crew"]
        for tf in text_fields:
            df[f"s_{tf}_raw"] = 0.0

    must_re = _must_watch_re(config.must_watch_keywords)
    w = config.component_weights

    def _apply_group(g: pd.DataFrame) -> pd.DataFrame:
        g = g.copy()
        g["s_channel"] = _zscore(g["s_channel_raw"])
        score = w.get("channel", 1.5) * g["s_channel"]

        if has_text:
            for tf in text_fields:
                g[f"s_{tf}"] = _zscore(g[f"s_{tf}_raw"])
                score = score + w.get(tf, 0.0) * g[f"s_{tf}"]

        g["final_score"] = score

        if must_re is not None:
            must = g["title"].fillna("").astype(str).str.contains(must_re)
            g["is_must_watch"] = must
            if must.any():
                g.loc[must, "final_score"] = g["final_score"].max() + 1.0
        else:
            g["is_must_watch"] = False

        g["rank_in_group"] = g["final_score"].rank(ascending=False, method="first").astype(int)
        return g

    df = (
        df.groupby(["date", "slot"], group_keys=True)
          .apply(_apply_group, include_groups=False)
          .reset_index()
    )

    return df.to_dict(orient="records")
