import argparse
import glob
import os
import sqlite3
import sys

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

REQUIRED = {"title", "Description", "Cast", "Crew", "channel", "user_selected"}


def main():
    parser = argparse.ArgumentParser(description="Import desktop-app training CSVs into SQLite.")
    parser.add_argument("--csv-dir", required=True, help="Folder containing training_full_*.csv files")
    parser.add_argument("--db-path", default="data/tv_scheduler.db", help="Path to SQLite database")
    parser.add_argument("--run-date", default="historical", help="run_date label to stamp on imported rows")
    args = parser.parse_args()

    paths = sorted(glob.glob(os.path.join(args.csv_dir, "training_full_*.csv")))
    if not paths:
        paths = sorted(glob.glob(os.path.join(args.csv_dir, "*.csv")))
    if not paths:
        print(f"No CSV files found in {args.csv_dir}")
        sys.exit(1)

    os.makedirs(os.path.dirname(args.db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(args.db_path)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS training (
            id INTEGER PRIMARY KEY,
            title TEXT, description TEXT, "cast" TEXT, crew TEXT,
            channel TEXT, selected INTEGER, run_date TEXT
        )
    """)
    conn.commit()

    total_imported = 0
    for path in paths:
        df = pd.read_csv(path)
        missing = REQUIRED - set(df.columns)
        if missing:
            print(f"  SKIP {os.path.basename(path)}: missing columns {missing}")
            continue

        df = df[list(REQUIRED)].copy()
        df = df.rename(columns={
            "Description": "description",
            "Cast": "cast",
            "Crew": "crew",
            "user_selected": "selected",
        })
        df["run_date"] = args.run_date
        df["selected"] = pd.to_numeric(df["selected"], errors="coerce").fillna(0).astype(int)

        df.to_sql("training", conn, if_exists="append", index=False)
        print(f"  imported {len(df):4d} rows from {os.path.basename(path)}")
        total_imported += len(df)

    conn.close()
    print(f"\nTotal: {total_imported} rows imported into {args.db_path}")
    print("Next step: python scripts/retrain.py --db-path", args.db_path)


if __name__ == "__main__":
    main()
