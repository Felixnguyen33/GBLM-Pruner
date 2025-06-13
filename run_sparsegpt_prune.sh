export CUDA_LAUNCH_BLOCKING=1
CUDA_VISIBLE_DEVICES=0,1,2,3 python main.py \
    --model Qwen/Qwen3-8B \
    --prune_method sparsegpt \
    --nsamples 128 \
    --seed 0 \
    --sparsity_ratio 0.5 \
    --sparsity_type unstructured \
    --save out/qwen_3_8b/unstructured/sparsegpt/ \
    --cache_dir /l/users/mukul.ranjan/GBLM_Pruner/llm_weights
