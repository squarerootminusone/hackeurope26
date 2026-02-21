# ML Model Optimization Guide

## 1. Mixed Precision Training/Inference
- Enable torch.cuda.amp or set dtype=torch.float16/bfloat16
- Use torch.autocast context manager for inference
- Expected impact: 1.5-2x speedup, <0.1% accuracy loss

## 2. torch.compile
- Wrap model in torch.compile(mode="reduce-overhead")
- Apply to the main model forward pass, not to data preprocessing
- Expected impact: 1.2-2x speedup, 0% accuracy loss

## 3. Data Loading
- Increase num_workers to at least 4 (ideally 8+)
- Enable pin_memory=True for GPU transfers
- Use persistent_workers=True to avoid worker respawn overhead
- Use prefetch_factor=2+
- Expected impact: reduces data loading bottleneck

## 4. Batch Size Optimization
- Increase batch size to maximize GPU memory utilization
- Monitor GPU memory usage and find the largest batch that fits
- Expected impact: better GPU utilization, throughput improvement

## 5. Quantization (if applicable)
- INT8 dynamic quantization for inference-only models
- Use torch.quantization.quantize_dynamic for linear layers
- Expected impact: 1.5-3x speedup, <0.5% accuracy loss

## 6. CUDA Optimizations
- torch.backends.cudnn.benchmark = True
- torch.backends.cuda.matmul.allow_tf32 = True
- torch.backends.cudnn.allow_tf32 = True
- Use torch.inference_mode() instead of torch.no_grad() where possible
- Expected impact: 1.1-1.3x speedup

## 7. Memory Optimizations
- Use gradient checkpointing for training
- Use torch.inference_mode() context manager for inference
- Clear CUDA cache between large operations if needed
- Expected impact: allows larger batch sizes

## 8. Model-Specific Optimizations
- For ViT models: use flash attention if available (torch >= 2.0)
- For transformer decoders: enable KV cache reuse
- For convnets: use channels_last memory format
- Expected impact: varies, can be significant for attention-heavy models

## Notes for the Optimizer
- Always preserve the original evaluation API (same inputs, same output format)
- Test that accuracy metrics remain within acceptable bounds
- Prefer optimizations that are well-tested and widely used
- Apply optimizations incrementally and document each change
- If an optimization requires a specific PyTorch version, note it in the changes
