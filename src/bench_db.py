"""
Lightweight DB writer for use inside benchmark containers.
Reads connection info from env vars (DB_HOST, DB_USER, DB_PASSWORD, DB_NAME).
Copy this file into Docker images alongside eval scripts.
"""

import json
import os
import pymysql


def write_benchmark_result(
    results: dict,
    module: str,
    variant: str,
    gpu_accelerator: str,
    job_name: str,
    dataset: str,
    metric_splits: list,
    metric_name: str = 'epe',
    extra_keys: list = None,
):
    """Write benchmark results to the benchmark_results table.

    Args:
        results: Dict with per-split metrics and a 'timing' key.
        module: Module name (e.g. 'raft', 'hamer').
        variant: 'baseline' or 'optimized'.
        gpu_accelerator: GKE accelerator label (e.g. 'nvidia-l4').
        job_name: K8s job name.
        dataset: Dataset name prefix (e.g. 'sintel'). Stored as '{dataset}_{split}'.
        metric_splits: List of splits to write (e.g. ['clean', 'final']).
        metric_name: Primary metric field name (default 'epe').
        extra_keys: Additional metric keys to store in extra_metrics JSON.
    """
    import torch

    db_host = os.environ.get('DB_HOST')
    db_user = os.environ.get('DB_USER')
    db_pass = os.environ.get('DB_PASSWORD')
    db_name = os.environ.get('DB_NAME')
    if not all([db_host, db_user, db_pass, db_name]):
        print("WARNING: DB env vars not set, skipping DB write")
        return

    gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu'
    timing = results['timing']

    conn = pymysql.connect(host=db_host, user=db_user, password=db_pass, database=db_name)
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
        conn.close()
