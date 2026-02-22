"""
Optimized RAFT benchmark evaluation.
Applies torch.compile (opt 1) and channels_last (opt 12) on top of
patched raft.py (opt 3) and corr.py (opt 4).
"""
import sys
sys.path.append('core')

import argparse
import os
import time
import json
import numpy as np
import torch

import datasets
from raft import RAFT
from utils.utils import InputPadder


@torch.no_grad()
def validate_sintel(model, iters=32):
    model.eval()
    results = {}
    total_time = 0.0
    total_pairs = 0

    for dstype in ['clean', 'final']:
        val_dataset = datasets.MpiSintel(split='training', dstype=dstype)
        epe_list = []

        for val_id in range(len(val_dataset)):
            image1, image2, flow_gt, _ = val_dataset[val_id]
            image1 = image1[None].cuda()
            image2 = image2[None].cuda()

            # OPT 12: Convert inputs to channels_last
            image1 = image1.to(memory_format=torch.channels_last)
            image2 = image2.to(memory_format=torch.channels_last)

            padder = InputPadder(image1.shape)
            image1, image2 = padder.pad(image1, image2)

            torch.cuda.synchronize()
            t0 = time.perf_counter()
            flow_low, flow_pr = model(image1, image2, iters=iters, test_mode=True)
            torch.cuda.synchronize()
            t1 = time.perf_counter()

            total_time += (t1 - t0)
            total_pairs += 1

            flow = padder.unpad(flow_pr[0]).cpu()
            epe = torch.sum((flow - flow_gt)**2, dim=0).sqrt()
            epe_list.append(epe.view(-1).numpy())

        epe_all = np.concatenate(epe_list)
        epe = np.mean(epe_all)
        px1 = np.mean(epe_all < 1)
        px3 = np.mean(epe_all < 3)
        px5 = np.mean(epe_all < 5)

        print("Validation (%s) EPE: %f, 1px: %f, 3px: %f, 5px: %f" % (dstype, epe, px1, px3, px5))
        results[dstype] = {
            'epe': float(epe),
            '1px': float(px1),
            '3px': float(px3),
            '5px': float(px5),
        }

    results['timing'] = {
        'total_time_sec': total_time,
        'total_pairs': total_pairs,
        'pairs_per_sec': total_pairs / total_time,
        'avg_ms_per_pair': (total_time / total_pairs) * 1000,
    }
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True, help="checkpoint path")
    parser.add_argument('--dataset', default='sintel')
    parser.add_argument('--iters', type=int, default=32)
    parser.add_argument('--small', action='store_true', help='use small model')
    parser.add_argument('--mixed_precision', action='store_true')
    parser.add_argument('--alternate_corr', action='store_true')
    parser.add_argument('--results_file', default='results/optimized_results.json')
    parser.add_argument('--gpu_accelerator', default='unknown')
    parser.add_argument('--job_name', default='unknown')
    args = parser.parse_args()

    # Enable cudnn benchmark
    torch.backends.cudnn.benchmark = True

    # Load model
    model = torch.nn.DataParallel(RAFT(args))
    model.load_state_dict(torch.load(args.model))
    model = model.module
    model.cuda()
    model.eval()

    # OPT 12: Convert model to channels_last memory format
    model = model.to(memory_format=torch.channels_last)

    # OPT 1: Apply torch.compile with reduce-overhead mode
    model = torch.compile(model, mode="reduce-overhead")

    # Warm-up (trigger torch.compile tracing + CUDA graph capture)
    # Sintel images are 436x1024, padded to 440x1024
    print("Warming up torch.compile ...")
    dummy1 = torch.randn(1, 3, 440, 1024, device='cuda').to(memory_format=torch.channels_last)
    dummy2 = torch.randn(1, 3, 440, 1024, device='cuda').to(memory_format=torch.channels_last)
    for _ in range(3):
        model(dummy1, dummy2, iters=args.iters, test_mode=True)
    torch.cuda.synchronize()
    print("Warm-up complete.")

    if args.dataset == 'sintel':
        results = validate_sintel(model, iters=args.iters)
    else:
        raise ValueError(f"Unsupported dataset for benchmark: {args.dataset}")

    print("\n=== OPTIMIZED RESULTS ===")
    print("Optimizations: torch.compile (1), skip intermediate upsample (3), cached delta grid (4), channels_last (12)")
    for key in ['clean', 'final']:
        if key in results:
            r = results[key]
            print(f"  {key}: EPE={r['epe']:.4f}, 1px={r['1px']:.4f}")
    t = results['timing']
    print(f"  Throughput: {t['pairs_per_sec']:.2f} pairs/sec ({t['avg_ms_per_pair']:.1f} ms/pair)")

    os.makedirs(os.path.dirname(args.results_file), exist_ok=True)
    with open(args.results_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"Results saved to {args.results_file}")

    # Write to DB
    try:
        from bench_db import write_benchmark_result
        write_benchmark_result(
            results, module='raft', variant='optimized',
            gpu_accelerator=args.gpu_accelerator, job_name=args.job_name,
            dataset=args.dataset, metric_splits=['clean', 'final'],
            metric_name='epe', extra_keys=['1px', '3px', '5px'],
        )
    except Exception as e:
        print(f"WARNING: DB write failed: {e}")


if __name__ == '__main__':
    main()
