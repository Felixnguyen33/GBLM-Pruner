export CUDA_LAUNCH_BLOCKING=0
CUDA_VISIBLE_DEVICES=0,1,2 python gradient_computation.py \
    --nsamples 128 \
    --model "llava-hf/llava-v1.6-vicuna-7b-hf" \
    --model_with_version llava_1_6_vicuna_7b \
    --cache_dir ~/ugrip/gwen/GBLM-Pruner/llm_weights \
    --gradient_path ~/ugrip/gwen/GBLM-Pruner/gradients


