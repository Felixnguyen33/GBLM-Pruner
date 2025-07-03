export CUDA_LAUNCH_BLOCKING=1
CUDA_VISIBLE_DEVICES=1 python main.py \
    --model "llava-hf/llava-v1.6-vicuna-7b-hf" \
    --prune_method sparsegpt \
    --nsamples 1 \
    --seed 0 \
    --sparsity_ratio 0.5 \
    --sparsity_type unstructured \
    --save out/llava_1_6_vicuna_7b/unstructured/sparsegpt/ \
    --cache_dir ~/ugrip/gwen/GBLM-Pruner/llm_weights \
    --save_model out/llava_1_6_vicuna_7b/unstructured/sparsegpt/



