export CUDA_LAUNCH_BLOCKING=1
CUDA_VISIBLE_DEVICES=1,2,3 python main.py \
    --model meta-llama/Llama-3.1-8B  \
    --gradient_path /l/users/mukul.ranjan/GBLM_Pruner/gradients/llama3.1/gradients_aggregrate_norm_l2_model_Llama-3.1-8B.pth \
    --cache_dir /l/users/mukul.ranjan/GBLM_Pruner/llm_weights \
    --prune_method gblm \
    --nsamples 128 \
    --seed 0 \
    --sparsity_ratio 0.5 \
    --sparsity_type unstructured \
    --save out/llama_3.1_8b_l2/unstructured/gblm/

