"""
Evaluation script for optimized HaMeR model.
Loads the optimized model (torch.compile + AMP + early stopping),
runs eval on FreiHAND-VAL, and compares timing with baseline.
"""

import argparse
import time
import json
import os
import sys

import torch
from tqdm import tqdm

# Add hamer root to path
sys.path.insert(0, '/app/hamer')

from hamer.configs import CACHE_DIR_HAMER, get_config
from hamer.models import download_models, load_hamer
from hamer.datasets import create_dataset
from hamer.utils import Evaluator, recursive_to

# Import from the optimized module
from hamer.models.optimized import load_hamer_optimized

DEFAULT_CHECKPOINT = os.path.join(CACHE_DIR_HAMER, 'hamer_ckpts/checkpoints/hamer.ckpt')


def datasets_eval_config():
    from yacs.config import CfgNode
    import yaml
    cfg_path = os.path.join(os.path.dirname(__file__), '..', 'hamer', 'configs', 'datasets_eval.yaml')
    if not os.path.exists(cfg_path):
        cfg_path = '/app/hamer/hamer/configs/datasets_eval.yaml'
    with open(cfg_path, 'r') as f:
        data = yaml.safe_load(f)
    out = {}
    for k, v in data.items():
        out[k] = CfgNode(v)
    return out


def run_eval(model, model_cfg, dataset_cfg, device, args, label=""):
    """Run evaluation and return timing + results."""
    rescale_factor = dataset_cfg.get('RESCALE_FACTOR', -1)
    metrics = dataset_cfg.get('METRICS', None)
    preds = dataset_cfg.get('PREDS', ['vertices', 'keypoints_3d'])
    pck_thresholds = dataset_cfg.get('PCK_THRESHOLDS', None)

    dataset = create_dataset(model_cfg, dataset_cfg, train=False, rescale_factor=rescale_factor)
    dataloader = torch.utils.data.DataLoader(
        dataset, args.batch_size, shuffle=False, num_workers=args.num_workers
    )

    evaluator = Evaluator(
        dataset_length=len(dataset),
        dataset=args.dataset,
        keypoint_list=dataset_cfg.KEYPOINT_LIST,
        pelvis_ind=model_cfg.EXTRA.PELVIS_IND,
        metrics=metrics,
        preds=preds,
        pck_thresholds=pck_thresholds,
    )

    # Warmup
    warmup_batch = next(iter(dataloader))
    warmup_batch = recursive_to(warmup_batch, device)
    with torch.no_grad():
        model(warmup_batch)
    if torch.cuda.is_available():
        torch.cuda.synchronize()

    # Timed evaluation
    start = time.time()
    for i, batch in enumerate(tqdm(dataloader, desc=f"Eval {label}")):
        batch = recursive_to(batch, device)
        with torch.no_grad():
            out = model(batch)
        evaluator(out, batch)
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    elapsed = time.time() - start

    # Save results
    results_dir = os.path.join(args.results_folder, label.lower().replace(' ', '_'))
    os.makedirs(results_dir, exist_ok=True)
    evaluator.log()

    return elapsed, len(dataset)


def main():
    parser = argparse.ArgumentParser(description='Evaluate optimized HaMeR model')
    parser.add_argument('--checkpoint', type=str, default=DEFAULT_CHECKPOINT)
    parser.add_argument('--results_folder', type=str, default='results')
    parser.add_argument('--dataset', type=str, default='FREIHAND-VAL')
    parser.add_argument('--batch_size', type=int, default=16)
    parser.add_argument('--num_workers', type=int, default=8)
    parser.add_argument('--skip_baseline', action='store_true', help='Skip baseline comparison')
    args = parser.parse_args()

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Using device: {device}")

    download_models(CACHE_DIR_HAMER)

    dataset_cfgs = datasets_eval_config()
    dataset_cfg = dataset_cfgs[args.dataset]

    results = {}

    # --- Baseline ---
    if not args.skip_baseline:
        print("\n=== BASELINE MODEL ===")
        model_base, model_cfg_base = load_hamer(args.checkpoint)
        model_base = model_base.to(device)
        model_base.eval()
        t_base, n_samples = run_eval(model_base, model_cfg_base, dataset_cfg, device, args, label="Baseline")
        results['baseline'] = {
            'time_sec': t_base,
            'samples': n_samples,
            'samples_per_sec': n_samples / t_base,
        }
        del model_base
        torch.cuda.empty_cache()
        print(f"Baseline: {t_base:.2f}s ({n_samples / t_base:.1f} samples/s)")

    # --- Optimized ---
    print("\n=== OPTIMIZED MODEL ===")
    model_opt, model_cfg_opt = load_hamer_optimized(args.checkpoint)
    model_opt = model_opt.to(device)
    model_opt.eval()
    t_opt, n_samples = run_eval(model_opt, model_cfg_opt, dataset_cfg, device, args, label="Optimized")
    results['optimized'] = {
        'time_sec': t_opt,
        'samples': n_samples,
        'samples_per_sec': n_samples / t_opt,
    }
    print(f"Optimized: {t_opt:.2f}s ({n_samples / t_opt:.1f} samples/s)")

    # --- Summary ---
    if 'baseline' in results:
        speedup = results['baseline']['time_sec'] / results['optimized']['time_sec']
        results['speedup'] = speedup
        print(f"\nSpeedup: {speedup:.2f}x")

    results_path = os.path.join(args.results_folder, 'timing_comparison.json')
    os.makedirs(args.results_folder, exist_ok=True)
    with open(results_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {results_path}")


if __name__ == '__main__':
    main()
