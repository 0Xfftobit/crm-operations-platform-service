"""Spark batch disparity job (design §5 — nightly fair-lending + redlining pattern analysis).

Reads decision events (parquet or JDBC), does the distributed group-by in Spark, then applies the
pure 4/5ths aggregation from ``app.spark_batch`` and writes ``fact_disparity``. Run with:

    spark-submit jobs/disparity_batch.py \
        --input s3a://warehouse/decision_events/ \
        --output s3a://warehouse/fact_disparity/ \
        --min-sample 100

Each input row is expected to carry ``sub_agent``, ``cohort_dimension``, ``cohort``, and a
boolean/int ``selected`` (1 = the favourable outcome, e.g. approved/contacted). The job is
deliberately thin — the compliance logic lives in the unit-tested pure function so batch and
streaming stay consistent.
"""
import argparse
import json
import sys


def _spark_run(input_path: str, output_path: str, min_sample: int) -> None:  # pragma: no cover
    from pyspark.sql import SparkSession
    from pyspark.sql import functions as F

    from app.spark_batch import disparity_from_counts

    spark = SparkSession.builder.appName("compliance-disparity-batch").getOrCreate()
    df = spark.read.parquet(input_path) if input_path.endswith("/") or "parquet" in input_path \
        else spark.read.format("jdbc").option("url", input_path).load()

    agg = (df.groupBy("sub_agent", "cohort_dimension", "cohort")
             .agg(F.sum(F.col("selected").cast("int")).alias("selected"),
                  F.count(F.lit(1)).alias("total")))
    counts = [r.asDict() for r in agg.collect()]         # small aggregate → driver
    results = disparity_from_counts(counts, min_sample=min_sample)

    out = spark.createDataFrame([
        {**r, "selection_rates": json.dumps(r["selection_rates"])} for r in results])
    out.write.mode("overwrite").parquet(output_path)
    spark.stop()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Compliance batch disparity analysis")
    ap.add_argument("--input", required=True, help="parquet path or JDBC URL of decision events")
    ap.add_argument("--output", required=True, help="output parquet path for fact_disparity")
    ap.add_argument("--min-sample", type=int, default=100)
    args = ap.parse_args(argv)
    _spark_run(args.input, args.output, args.min_sample)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
