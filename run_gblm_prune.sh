export CUDA_LAUNCH_BLOCKING=1
CUDA_VISIBLE_DEVICES=6,7 python main.py \
    --model llava-hf/llava-v1.6-vicuna-7b-hf \
    --gradient_path ~/ugrip/gwen/GBLM-Pruner/gradients/llava_1_6_vicuna_7b/gradients_aggregrate_norm_l1_model_llava-v1.6-vicuna-7b-hf.pth \
    --cache_dir ~/ugrip/gwen/GBLM-Pruner/llm_weights \
    --prune_method gblm \
    --nsamples 128 \
    --seed 0 \
    --sparsity_ratio 0.5 \
    --sparsity_type unstructured \
    --save out/llava_1_6_vicuna_7b/unstructured/gblm/ \
    --save_model out/llava_1_6_vicuna_7b/unstructured/gblm/