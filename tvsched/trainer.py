from __future__ import annotations

import os
from datetime import datetime

import joblib
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline

MODEL_PATH = os.environ.get("MODEL_PATH", "/mnt/data/models/tv_component_models.joblib")
MIN_SELECTED = 5


def _build_model() -> Pipeline:
    return Pipeline([
        ("tfidf", TfidfVectorizer(
            max_features=30000,
            ngram_range=(1, 2),
            min_df=1,
            strip_accents="unicode",
            sublinear_tf=True,
        )),
        ("ridge", Ridge(alpha=3.0, random_state=0)),
    ])


def retrain_and_save(training_rows: list[dict]) -> bool:
    n_selected = sum(1 for r in training_rows if int(r.get("selected", 0)) == 1)
    if n_selected < MIN_SELECTED:
        print(f"Retraining skipped: only {n_selected} selected shows (need {MIN_SELECTED})")
        return False

    df = pd.DataFrame(training_rows)
    y = pd.to_numeric(df["selected"], errors="coerce").fillna(0.0)

    if y.nunique() <= 1:
        print("Retraining skipped: all labels identical")
        return False

    field_map = {
        "title": "title",
        "description": "Description",
        "cast": "Cast",
        "crew": "Crew",
    }

    models: dict = {}
    for db_col, model_key in field_map.items():
        col_data = df[db_col].fillna("").astype(str) if db_col in df.columns else pd.Series([""] * len(df))
        m = _build_model()
        m.fit(col_data, y)
        models[model_key] = m

    if "channel" in df.columns:
        channel_means = df.assign(_y=y).groupby("channel")["_y"].mean().to_dict()
    else:
        channel_means = {}
    models["channel_encoder"] = {"global_mean": float(y.mean()), "channel_mean": channel_means}

    n_selected = int((y == 1).sum())
    models["meta"] = {
        "text_fields": list(field_map.values()),
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "n_rows": len(df),
        "n_selected": n_selected,
    }

    os.makedirs(os.path.dirname(MODEL_PATH) or ".", exist_ok=True)
    joblib.dump(models, MODEL_PATH)
    print(f"Model retrained: {len(df)} rows, {n_selected} selected -> {MODEL_PATH}")
    return True
