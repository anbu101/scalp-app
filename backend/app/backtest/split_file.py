"""
split_file.py

Converts the large NIFTY option-chain CSV into compressed Parquet files,
partitioned by expiry date. DuckDB streams the CSV off disk, so RAM never
sees the whole 6.82 GB file.

Output: one folder per expiry under OUT_DIR, e.g.
    split_out/expiry=2024-10-03/data_0.parquet
"""

import os
import sys

try:
    import duckdb
except ImportError:
    sys.exit(
        "duckdb is not installed. Run:\n"
        "    pip3 install duckdb --break-system-packages"
    )

SRC = "/Users/anbu/Downloads/final_merged_output.csv"
OUT_DIR = "/Users/anbu/dev/scalp-app/backend/app/backtest/split_out"


def main():
    if not os.path.exists(SRC):
        sys.exit(f"Source file not found: {SRC}")

    os.makedirs(OUT_DIR, exist_ok=True)

    con = duckdb.connect()

    # --- Step 1: sanity check before writing anything -----------------------
    print("Inspecting file (this reads only what it needs)...\n")

    schema = con.sql(
        f"SELECT * FROM read_csv_auto('{SRC}') LIMIT 5"
    ).df()
    print("First 5 rows:")
    print(schema.to_string())
    print("\nColumns:", list(schema.columns), "\n")

    stats = con.sql(f"""
        SELECT
            COUNT(*)                       AS total_rows,
            COUNT(DISTINCT expiry)         AS distinct_expiries,
            COUNT(DISTINCT strike_price)   AS distinct_strikes,
            MIN(timestamp)                 AS first_ts,
            MAX(timestamp)                 AS last_ts
        FROM read_csv_auto('{SRC}')
    """).df()
    print("File summary:")
    print(stats.to_string(index=False))
    print()

    # --- Step 2: write partitioned Parquet ----------------------------------
    print(f"Writing partitioned Parquet to:\n    {OUT_DIR}\n")
    print("This is the slow part — it streams the whole file once. Please wait...\n")

    con.sql(f"""
        COPY (
            SELECT * FROM read_csv_auto('{SRC}')
        )
        TO '{OUT_DIR}'
        (FORMAT parquet, PARTITION_BY (expiry), OVERWRITE_OR_IGNORE)
    """)

    print("Done.")
    print(f"Parquet files are under: {OUT_DIR}")
    print("\nEach expiry is now its own folder. To query the whole set later:")
    print(f"    duckdb.sql(\"SELECT * FROM read_parquet('{OUT_DIR}/**/*.parquet')\")")


if __name__ == "__main__":
    main()