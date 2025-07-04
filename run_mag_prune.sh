python main.py \
    --model Qwen/Qwen3-8B \
    --prune_method magnitude \
    --nsamples 128 \
    --seed 0 \
    --sparsity_ratio 0.0 \
    --sparsity_type unstructured \
    --save out/qwen_3_8b/unstructured/magnitude/ \
    --cache_dir /l/users/mukul.ranjan/GBLM_Pruner/llm_weights
