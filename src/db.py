"""Database connection helper using Cloud SQL and Secret Manager."""

import json
import os
import pymysql
from google.cloud import secretmanager

PROJECT = "data-platform-dev-486916"
SECRET_ID = "eval-db-password"
DB_HOST = "34.38.118.228"
DB_USER = "eval_user"
DB_NAME = "evaluations_db"


def get_db_password() -> str:
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{PROJECT}/secrets/{SECRET_ID}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("utf-8")


def get_connection() -> pymysql.Connection:
    """Get DB connection using Secret Manager (for orchestration code)."""
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=get_db_password(),
        database=DB_NAME,
    )


def get_connection_from_env() -> pymysql.Connection:
    """Get DB connection from env vars (for use inside containers)."""
    return pymysql.connect(
        host=os.environ['DB_HOST'],
        user=os.environ['DB_USER'],
        password=os.environ['DB_PASSWORD'],
        database=os.environ['DB_NAME'],
    )


def write_benchmark_result(
    results: dict,
    module: str,
    variant: str,
    gpu_accelerator: str,
    job_name: str,
    dataset: str,
    metric_splits: list[str],
    metric_name: str = 'epe',
    extra_keys: list[str] | None = None,
    conn: pymysql.Connection | None = None,
):
    """Write benchmark results to the benchmark_results table.

    Args:
        results: Dict with per-split metrics and a 'timing' key.
            Each split should have at least `metric_name` and any `extra_keys`.
            timing should have: total_time_sec, total_pairs, pairs_per_sec, avg_ms_per_pair.
        module: Module name (e.g. 'raft', 'hamer').
        variant: 'baseline' or 'optimized'.
        gpu_accelerator: GKE accelerator label (e.g. 'nvidia-l4').
        job_name: K8s job name.
        dataset: Dataset name prefix (e.g. 'sintel'). Stored as '{dataset}_{split}'.
        metric_splits: List of splits to write (e.g. ['clean', 'final']).
        metric_name: Primary metric field name (default 'epe').
        extra_keys: Additional metric keys to store in extra_metrics JSON.
        conn: Optional existing connection. If None, connects from env vars.
    """
    import torch

    close_conn = False
    if conn is None:
        conn = get_connection_from_env()
        close_conn = True

    gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu'
    timing = results['timing']

    try:
        with conn.cursor() as cur:
            for split in metric_splits:
                if split not in results:
                    continue
                r = results[split]
                extra = {}
                for k in (extra_keys or []):
                    if k in r:
                        extra[k] = r[k]
                cur.execute(
                    """INSERT INTO benchmark_results
                    (module, variant, gpu, gpu_accelerator, dataset, metric_name, metric_value,
                     throughput, avg_latency_ms, eval_time_sec, total_pairs, extra_metrics, job_name)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (module, variant, gpu, gpu_accelerator, f'{dataset}_{split}',
                     metric_name, r[metric_name],
                     timing['pairs_per_sec'], timing['avg_ms_per_pair'],
                     timing['total_time_sec'], timing['total_pairs'],
                     json.dumps(extra) if extra else None, job_name)
                )
        conn.commit()
        print(f"Results written to DB ({conn.host})")
    finally:
        if close_conn:
            conn.close()
