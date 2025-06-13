export CUDA_LAUNCH_BLOCKING=1
CUDA_VISIBLE_DEVICES=0,1,2,3 python gradient_computation.py \
    --nsamples 128 \
    --model Qwen/Qwen3-8B \
    --model_with_version qwen_3_8b \
    --cache_dir /l/users/mukul.ranjan/GBLM_Pruner/llm_weights \
    --gradient_path /l/users/mukul.ranjan/GBLM_Pruner/gradients