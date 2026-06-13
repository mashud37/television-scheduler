import argparse
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tvsched"))


def main():
    parser = argparse.ArgumentParser(description="Retrain ranking model from local SQLite training data.")
    parser.add_argument("--db-path", default="data/tv_scheduler.db")
    parser.add_argument("--out", default="assets/models/tv_component_models.joblib",
                        help="Output path for the retrained model")
    parser.add_argument("--upload", metavar="GCS_URI",
                        help="After training, upload to this GCS path (e.g. gs://bucket/models/...)")
    args = parser.parse_args()

    os.environ.setdefault("DB_PATH", args.db_path)
    os.environ.setdefault("MODEL_PATH", args.out)

    from db import get_training_data, get_training_stats, init_db
    from trainer import retrain_and_save, MIN_SELECTED

    init_db()
    total, n_selected = get_training_stats()
    print(f"Training table: {total} rows, {n_selected} selected")

    if n_selected < MIN_SELECTED:
        print(f"Need at least {MIN_SELECTED} selected shows to retrain (have {n_selected}).")
        print("Run scripts/import_historical.py first, or make more selections via the web UI.")
        sys.exit(1)

    training_rows = get_training_data()
    success = retrain_and_save(training_rows)

    if not success:
        print("Retraining failed — check output above.")
        sys.exit(1)

    print(f"Model saved to: {args.out}")

    if args.upload:
        print(f"Uploading to {args.upload} ...")
        ret = subprocess.run(
            ["gcloud", "storage", "cp", args.out, args.upload],
            check=False,
        ).returncode
        if ret != 0:
            print("Upload failed — check gcloud auth and bucket permissions.")
            sys.exit(1)
        print("Upload complete.")


if __name__ == "__main__":
    main()
