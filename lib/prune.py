#prune.py
import time 
import heapq 
import torch 
import torch.nn as nn 
from .sparsegpt import SparseGPT 
from .layerwrapper import WrappedGPT
from .data import get_loaders 
from torch.utils.data import DataLoader
import torch.nn.functional as F
from torch.optim import AdamW
import numpy as np
import matplotlib.pyplot as plt
import gc
import csv
import os

from pdb import set_trace as st 

def get_hidden_size(model):
    """
    Get the hidden size from different model configurations.
    Handles various model architectures including LLaVA, Qwen, etc.
    """
    # For LLaVA and similar VLMs
    if hasattr(model, "language_model") and hasattr(model.language_model, "config"):
        return model.language_model.config.hidden_size
    # For standard LLMs
    elif hasattr(model, "config") and hasattr(model.config, "hidden_size"):
        return model.config.hidden_size
    # For models with model.config
    elif hasattr(model, "model") and hasattr(model.model, "config") and hasattr(model.model.config, "hidden_size"):
        return model.model.config.hidden_size
    else:
        raise AttributeError("Cannot find hidden_size in the model configuration.")

def no_zero(data):
    zero_count = (data == 0).sum().item()
    return zero_count

def plot_subsampled_matrix_and_save(matrix, output_prefix, subsample_factor):
    odd_subsampled_matrix = matrix[::subsample_factor, ::subsample_factor]
    even_subsampled_matrix = matrix[1::subsample_factor, 1::subsample_factor]
    ones_matrix = np.ones_like(odd_subsampled_matrix)
    zeros_matrix = np.zeros_like(even_subsampled_matrix)
    # print(ones_matrix)
    # print(zeros_matrix)
    plt.figure(figsize=(20, 10))
    
    plt.subplot(2, 2, 1)
    plt.imshow(odd_subsampled_matrix, cmap='gray', interpolation='nearest')
    plt.title('Odd Subsampling')
    plt.grid(which='both', color='black', linewidth=1)
    plt.xticks([])
    plt.yticks([])
    
    plt.subplot(2, 2, 2)
    plt.imshow(even_subsampled_matrix, cmap='gray', interpolation='nearest')
    plt.title('Even Subsampling')
    plt.grid(which='both', color='black', linewidth=1)
    plt.xticks([])
    plt.yticks([])
    
    plt.subplot(2, 2, 3)
    plt.imshow(ones_matrix, cmap='gray', interpolation='nearest')
    plt.title('All Ones')
    plt.grid(which='both', color='black', linewidth=1)
    plt.xticks([])
    plt.yticks([])
    
    plt.subplot(2, 2, 4)
    plt.imshow(zeros_matrix, cmap='gray_r', interpolation='nearest')
    plt.title('All Zeros')
    plt.grid(which='both', color='black', linewidth=1)
    plt.xticks([])
    plt.yticks([])
    
    plt.tight_layout()
    plt.savefig(output_prefix + '_subsampled_plots.png', dpi=300)
    plt.clf()  # Clear the figure after saving


def find_layers(module, layers=[nn.Linear], name=''):
    """
    Recursively find the layers of a certain type in a module.

    Args:
        module (nn.Module): PyTorch module.
        layers (list): List of layer types to find.
        name (str): Name of the module.

    Returns:
        dict: Dictionary of layers of the given type(s) within the module.
    """
    if type(module) in layers:
        return {name: module}
    res = {}
    for name1, child in module.named_children():
        res.update(find_layers(
            child, layers=layers, name=name + '.' + name1 if name != '' else name1
        ))
    return res

def check_sparsity(model, args):
    use_cache = getattr(model.config, "use_cache", False)
    setattr(model.config, "use_cache", False)

    layers = get_lm_layers(model)
    count = 0 
    total_params = 0
    for i in range(len(layers)):
        layer = layers[i]
        subset = find_layers(layer)

        sub_count = 0
        sub_params = 0
        for name in subset:
            W = subset[name].weight.data
            count += (W==0).sum().item()
            total_params += W.numel()

            sub_count += (W==0).sum().item()
            sub_params += W.numel()

        print(f"layer {i} sparsity {float(sub_count)/sub_params:.6f}")

    setattr(model.config, "use_cache", use_cache)
    return float(count)/total_params 

def prepare_calibration_input(model, dataloader, nsamples, device):
    use_cache = getattr(model.config, "use_cache", False)
    setattr(model.config, "use_cache", False)
    layers = get_lm_layers(model)

    # Determine the device for embedding tokens for different model structures
    if hasattr(model, 'hf_device_map'):
        # Try different possible device map keys for embedding layer
        possible_embed_keys = [
            "model.language_model.embed_tokens",  # For Qwen2.5-VL and similar VLMs
            "model.embed_tokens",  # For standard LLMs
            "model.language_model.model.embed_tokens"  # For LLaVA-style models
        ]
        for key in possible_embed_keys:
            if key in model.hf_device_map:
                device = model.hf_device_map[key]
                break

    dtype = next(iter(model.parameters())).dtype
    hidden_size = get_hidden_size(model)
    inps = torch.zeros((nsamples, model.seqlen, hidden_size), dtype=dtype, device=device)
    inps.requires_grad = False
    cache = {'i': 0, 'attention_mask': None, "position_embeddings": None}

    class Catcher(nn.Module):
        def __init__(self, module):
            super().__init__()
            self.module = module
        
        def __getattr__(self, name):
            try:
                return super().__getattr__(name)
            except AttributeError:
                return getattr(self.module, name)
        
        def forward(self, inp, **kwargs):
            # print(">>>> kwargs >>>>>>>>>")
            # print(kwargs)
            # print(">>>> kwargs >>>>>>>>>")
            inps[cache['i']] = inp
            cache['i'] += 1
            cache['attention_mask'] = kwargs['attention_mask']
            cache['position_embeddings'] = kwargs['position_embeddings']
            raise ValueError
    # Replace the first layer with Catcher to capture input
    layers[0] = Catcher(layers[0])
    for batch in dataloader:
        try:
            model(batch[0].to(device))
        except ValueError:
            pass 
    layers[0] = layers[0].module

    outs = torch.zeros_like(inps)
    attention_mask = cache['attention_mask']
    position_embeddings = cache['position_embeddings']
    setattr(model.config, "use_cache", use_cache)

    return inps, outs, attention_mask, position_embeddings 

def return_given_alpha(alpha, sort_res, W_metric, tmp_metric, sum_before):
    thres_cumsum = sum_before * alpha 
    sort_mask = tmp_metric <= thres_cumsum.reshape((-1,1))
    thres = torch.gather(sort_res[0], dim=1, index=sort_mask.sum(dim=1, keepdims=True)-1)
    W_mask = (W_metric <= thres)
    cur_sparsity = (W_mask==True).sum() / W_mask.numel()
    return W_mask, cur_sparsity

def prune_magnitude(args, model, tokenizer, device=torch.device("cuda:0"), prune_n=0, prune_m=0, layer_no=-1):
    layers = get_lm_layers(model)

    for i in range(len(layers)):
        layer = layers[i]
        subset = find_layers(layer)

        for name in subset:
            W = subset[name].weight.data 
            W_metric = torch.abs(W)
            if prune_n != 0:
                W_mask = (torch.zeros_like(W)==1)
                for ii in range(W_metric.shape[1]):
                    if ii % prune_m == 0:
                        tmp = W_metric[:,ii:(ii+prune_m)].float()
                        W_mask.scatter_(1,ii+torch.topk(tmp, prune_n,dim=1, largest=False)[1], True)
            else:
                # thresh = torch.sort(W_metric.flatten().cuda())[0][int(W.numel()*args.sparsity_ratio)].cpu()
                thresh = torch.sort(W_metric.flatten())[0][int(W_metric.numel()*args.sparsity_ratio)].cpu()
                W_mask = (W_metric<=thresh)
            
            W[W_mask] = 0

def prune_gradient(args, model, tokenizer, device=torch.device("cuda:0"), prune_n=0, prune_m=0, layer_no=-1):

    layers = get_lm_layers(model)
    with open(args.gradient_path, 'rb') as file:
        gradients = torch.load(args.gradient_path, map_location=torch.device('cpu')) 
    
    for i in range(len(layers)):
        layer = layers[i]
        subset = find_layers(layer)

        for name in subset:
            indexed_name = f"{name}_layer_{i}"
            W = subset[name].weight.data 
            W_metric = torch.abs(W)
            if not args.gradient_inv:
                W_metric = W_metric.to(dtype=torch.float32) * torch.abs(gradients[indexed_name].to(device=W_metric.device)).to(dtype=torch.float32)#+ small_value)
            else:
                small_value = torch.tensor(1e-8, dtype=gradients[indexed_name].dtype, device=gradients[indexed_name].device)
                gradient_inv = 1 / (torch.abs(gradients[indexed_name]) + small_value)
                W_metric = W_metric.to(dtype=torch.float32) * gradient_inv.to(device=W_metric.device).to(dtype=torch.float32)
            W_mask = (torch.zeros_like(W)==1)
            if prune_n != 0:
                for ii in range(W_metric.shape[1]):
                    if ii % prune_m == 0:
                        tmp = W_metric[:,ii:(ii+prune_m)].float()
                        W_mask.scatter_(1,ii+torch.topk(tmp, prune_n,dim=1, largest=False)[1], True)
            else:
                sort_res = torch.sort(W_metric, dim=-1, stable=True)
                indices = sort_res[1][:,:int(W_metric.shape[1]*args.sparsity_ratio)]
                W_mask.scatter_(1, indices, True)

            W[W_mask] = 0


def prune_gblm(args, model, tokenizer, device=torch.device("cuda:0"), prune_n=0, prune_m=0, layer_no=-1):
    use_cache = getattr(model.config, "use_cache", False)
    setattr(model.config, "use_cache", False)
    with open(args.gradient_path, 'rb') as file:
        gradients = torch.load(args.gradient_path, map_location=torch.device('cpu')) 

    print("loading calibration data")
    dataloader, _ = get_loaders("c4",nsamples=args.nsamples,seed=args.seed,seqlen=2048,tokenizer=tokenizer)
    print("dataset loading complete")
    with torch.no_grad():
        inps, outs, attention_mask, position_embeddings = prepare_calibration_input(model, dataloader, args.nsamples, device)

    layers = get_lm_layers(model)
    for i in range(len(layers)):
        layer = layers[i]
        subset = find_layers(layer)

        # Handle device mapping for different model structures
        layer_device_key = None
        if hasattr(model, 'hf_device_map'):
            # Try different possible device map keys
            possible_keys = [
                f"model.language_model.layers.{i}",  # For Qwen2.5-VL and similar VLMs
                f"model.layers.{i}",  # For standard LLMs
                f"model.language_model.model.layers.{i}"  # For LLaVA-style models
            ]
            for key in possible_keys:
                if key in model.hf_device_map:
                    layer_device_key = key
                    break
        
        if layer_device_key:
            dev = model.hf_device_map[layer_device_key]
            # Device transfer
            inps = inps.to(dev)
            outs = outs.to(dev)
            if attention_mask is not None:
                print("attention mask is not none, shape is: ", attention_mask.shape)
                attention_mask = attention_mask.to(dev)

            if position_embeddings is not None:
                # position_embeddings = position_embeddings.to(dev)
                position_embeddings = tuple(t.to(dev) for t in position_embeddings)
        wrapped_layers = {}
        for name in subset:
            wrapped_layers[name] = WrappedGPT(subset[name], layer_id=i, layer_name=name)

        def add_batch(name):
            def tmp(_, inp, out):
                wrapped_layers[name].add_batch(inp[0].data, out.data)
            return tmp

        handles = []
        for name in wrapped_layers:
            handles.append(subset[name].register_forward_hook(add_batch(name))) ## this is a important function.
        for j in range(args.nsamples):
            with torch.no_grad():
                outs[j] = layer(inps[j].unsqueeze(0), attention_mask=attention_mask, position_embeddings=position_embeddings)[0]

        for h in handles:
            h.remove() 

        for sub_i, name in enumerate(subset):
            indexed_name = f"{name}_layer_{i}"
            print(f"pruning layer {i} name {name}")
            W_metric = torch.abs(subset[name].weight.data) * torch.sqrt(wrapped_layers[name].scaler_row.reshape((1,-1)))
            if not args.gradient_inv:
                # small_value = torch.tensor(1e-8, dtype=gradients[indexed_name].dtype, device=gradients[indexed_name].device)
                W_metric_grad = torch.abs(subset[name].weight.data)* torch.abs(gradients[indexed_name].to(device=W_metric.device))
                W_metric = W_metric.to(dtype=torch.float32) + W_metric_grad.to(dtype=torch.float32)  #+ small_value)
            else:
                small_value = torch.tensor(1e-8, dtype=gradients[indexed_name].dtype, device=gradients[indexed_name].device)
                gradient_inv = 1 / (torch.abs(gradients[indexed_name]) + small_value)
                W_metric = W_metric.to(dtype=torch.float32)  * gradient_inv.to(device=W_metric.device).to(dtype=torch.float32) 

            W_mask = (torch.zeros_like(W_metric) == 1)  ## initialize a mask to be all False
            if prune_n != 0:
                # structured n:m sparsity
                for ii in range(W_metric.shape[1]):
                    if ii % prune_m == 0:
                        tmp = W_metric[:,ii:(ii+prune_m)].float()
                        W_mask.scatter_(1,ii+torch.topk(tmp, prune_n,dim=1, largest=False)[1], True)
            else:
                sort_res = torch.sort(W_metric, dim=-1, stable=True)

                if args.use_variant:
                    # wanda variant 
                    tmp_metric = torch.cumsum(sort_res[0], dim=1)
                    sum_before = W_metric.sum(dim=1)

                    alpha = 0.4
                    alpha_hist = [0., 0.8]
                    W_mask, cur_sparsity = return_given_alpha(alpha, sort_res, W_metric, tmp_metric, sum_before)
                    while (torch.abs(cur_sparsity - args.sparsity_ratio)>0.001) and (alpha_hist[1]-alpha_hist[0]>=0.001):
                        if cur_sparsity > args.sparsity_ratio:
                            alpha_new = (alpha + alpha_hist[0]) / 2.0
                            alpha_hist[1] = alpha
                        else:
                            alpha_new = (alpha + alpha_hist[1]) / 2.0
                            alpha_hist[0] = alpha

                        alpha = alpha_new 
                        W_mask, cur_sparsity = return_given_alpha(alpha, sort_res, W_metric, tmp_metric, sum_before)
                    print(f"alpha found {alpha} sparsity {cur_sparsity:.6f}")
                else:
                    # unstructured pruning
                    indices = sort_res[1][:,:int(W_metric.shape[1]*args.sparsity_ratio)]
                    W_mask.scatter_(1, indices, True)

            subset[name].weight.data[W_mask] = 0  ## set weights to zero 

        for j in range(args.nsamples):
            with torch.no_grad():
                outs[j] = layer(inps[j].unsqueeze(0), attention_mask=None, position_embeddings=position_embeddings)[0]
        inps, outs = outs, inps

    setattr(model.config, "use_cache", use_cache)
    torch.cuda.empty_cache()


def prune_wanda(args, model, tokenizer, device=torch.device("cuda:0"), prune_n=0, prune_m=0, layer_no=-1):
    use_cache = getattr(model.config, "use_cache", False)
    setattr(model.config, "use_cache", False)

    print("loading calibration data")
    dataloader, _ = get_loaders("c4",nsamples=args.nsamples,seed=args.seed,seqlen=model.seqlen,tokenizer=tokenizer)
    print("dataset loading complete")
    with torch.no_grad():
        inps, outs, attention_mask, position_embeddings = prepare_calibration_input(model, dataloader, args.nsamples, device)

    layers = get_lm_layers(model)

    for i in range(len(layers)):
        layer = layers[i]
        subset = find_layers(layer)

        if f"model.layers.{i}" in model.hf_device_map:   ## handle the case for llama-30B and llama-65B, when the device map has multiple GPUs;
            dev = model.hf_device_map[f"model.layers.{i}"]
            # inps, outs, attention_mask, position_embeddings = inps.to(dev), outs.to(dev), attention_mask.to(dev), position_embeddings.to(dev)
            inps = inps.to(dev)
            outs = outs.to(dev)
            if attention_mask is not None:
                print("attention mask is not none, shape is: ", attention_mask.shape)
                attention_mask = attention_mask.to(dev)

            if position_embeddings is not None:
                # print("position_embeddings is not none, shape is:", position_embeddings.shape)
                position_embeddings = tuple(t.to(dev) for t in position_embeddings)
                # position_embeddings = position_embeddings.to(dev)

        wrapped_layers = {}
        for name in subset:
            wrapped_layers[name] = WrappedGPT(subset[name], layer_id=i, layer_name=name)

        def add_batch(name):
            def tmp(_, inp, out):
                wrapped_layers[name].add_batch(inp[0].data, out.data)
            return tmp

        handles = []
        for name in wrapped_layers:
            handles.append(subset[name].register_forward_hook(add_batch(name))) ## this is a important function.
        for j in range(args.nsamples):
            with torch.no_grad():
                outs[j] = layer(inps[j].unsqueeze(0), attention_mask=attention_mask, position_embeddings=position_embeddings)[0]

        for h in handles:
            h.remove() 

        for name in subset:
            print(f"pruning layer {i} name {name}")
            W_metric = torch.abs(subset[name].weight.data) * torch.sqrt(wrapped_layers[name].scaler_row.reshape((1,-1)))

            W_mask = (torch.zeros_like(W_metric) == 1)  ## initialize a mask to be all False
            if prune_n != 0:
                # structured n:m sparsity
                for ii in range(W_metric.shape[1]):
                    if ii % prune_m == 0:
                        tmp = W_metric[:,ii:(ii+prune_m)].float()
                        W_mask.scatter_(1,ii+torch.topk(tmp, prune_n,dim=1, largest=False)[1], True)
            else:
                sort_res = torch.sort(W_metric, dim=-1, stable=True)

                if args.use_variant:
                    # wanda variant 
                    tmp_metric = torch.cumsum(sort_res[0], dim=1)
                    sum_before = W_metric.sum(dim=1)

                    alpha = 0.4
                    alpha_hist = [0., 0.8]
                    W_mask, cur_sparsity = return_given_alpha(alpha, sort_res, W_metric, tmp_metric, sum_before)
                    while (torch.abs(cur_sparsity - args.sparsity_ratio)>0.001) and (alpha_hist[1]-alpha_hist[0]>=0.001):
                        if cur_sparsity > args.sparsity_ratio:
                            alpha_new = (alpha + alpha_hist[0]) / 2.0
                            alpha_hist[1] = alpha
                        else:
                            alpha_new = (alpha + alpha_hist[1]) / 2.0
                            alpha_hist[0] = alpha

                        alpha = alpha_new 
                        W_mask, cur_sparsity = return_given_alpha(alpha, sort_res, W_metric, tmp_metric, sum_before)
                    print(f"alpha found {alpha} sparsity {cur_sparsity:.6f}")
                else:
                    # unstructured pruning
                    indices = sort_res[1][:,:int(W_metric.shape[1]*args.sparsity_ratio)]
                    W_mask.scatter_(1, indices, True)

            subset[name].weight.data[W_mask] = 0  ## set weights to zero 

        for j in range(args.nsamples):
            with torch.no_grad():
                outs[j] = layer(inps[j].unsqueeze(0), attention_mask=attention_mask, position_embeddings=position_embeddings)[0]
        inps, outs = outs, inps

    setattr(model.config, "use_cache", use_cache)
    torch.cuda.empty_cache()


@torch.no_grad()
def prune_sparsegpt(args, model, tokenizer, device, prune_n=0, prune_m=0, layer_no=-1):
    ## SparseGPT code available at: https://github.com/IST-DASLab/sparsegpt/tree/f5c25005a61f96a0933ca2f95705a963585aafaa
    print('Starting ...')
    dataloader, _ = get_loaders("c4",nsamples=args.nsamples,seed=args.seed,seqlen=2048,tokenizer=tokenizer)

    use_cache = getattr(model.config, "use_cache", False)
    setattr(model.config, "use_cache", False)
    layers = get_lm_layers(model)

    # Determine the initial device for input preparation
    initial_dev = device
    if hasattr(model, 'hf_device_map'):
        # Try different possible device map keys for embedding layer
        possible_embed_keys = [
            "model.language_model.embed_tokens",  # For Qwen2.5-VL and similar VLMs
            "model.embed_tokens",  # For standard LLMs
            "model.language_model.model.embed_tokens"  # For LLaVA-style models
        ]
        for key in possible_embed_keys:
            if key in model.hf_device_map:
                initial_dev = model.hf_device_map[key]
                break

    dtype = next(iter(model.parameters())).dtype
    hidden_size = get_hidden_size(model)
    inps = torch.zeros(
        (args.nsamples, model.seqlen, hidden_size), dtype=dtype, device=initial_dev
    )
    cache = {'i': 0, 'attention_mask': None, "position_embeddings": None}

    class Catcher(nn.Module):
        def __init__(self, module):
            super().__init__()
            self.module = module
        
        def __getattr__(self, name):
            try:
                return super().__getattr__(name)
            except AttributeError:
                return getattr(self.module, name)
        
        def forward(self, inp, **kwargs):
            inps[cache['i']] = inp
            cache['i'] += 1
            cache['attention_mask'] = kwargs['attention_mask']
            cache['position_embeddings'] = kwargs['position_embeddings']
            raise ValueError
    layers[0] = Catcher(layers[0])
    for batch in dataloader:
        try:
            model(batch[0].to(initial_dev))
        except ValueError:
            pass
    layers[0] = layers[0].module
    setattr(model.config, "use_cache", use_cache)

    outs = torch.zeros_like(inps)
    attention_mask = cache['attention_mask']
    position_embeddings = cache['position_embeddings']

    print('Ready.')

    for i in range(len(layers)):
        layer = layers[i]
        # Determine the device for this layer
        layer_device_key = None
        if hasattr(model, 'hf_device_map'):
            # Try different possible device map keys
            possible_keys = [
                f"model.language_model.layers.{i}",  # For Qwen2.5-VL and similar VLMs
                f"model.layers.{i}",  # For standard LLMs
                f"model.language_model.model.layers.{i}"  # For LLaVA-style models
            ]
            for key in possible_keys:
                if key in model.hf_device_map:
                    layer_device_key = key
                    break
        
        if layer_device_key:
            layer_dev = model.hf_device_map[layer_device_key]
            print(f"layer {i} device {layer_dev}")
            # Device transfer
            inps = inps.to(layer_dev)
            outs = outs.to(layer_dev)
            if attention_mask is not None:
                print("attention mask is not none, shape is: ", attention_mask.shape)
                attention_mask = attention_mask.to(layer_dev)
            if position_embeddings is not None:
                position_embeddings = tuple(t.to(layer_dev) for t in position_embeddings)
        else:
            layer_dev = device

        subset = find_layers(layer)

        gpts = {}
        for name in subset:
            gpts[name] = SparseGPT(subset[name])

        def add_batch(name):
            def tmp(_, inp, out):
                gpts[name].add_batch(inp[0].data, out.data)
            return tmp

        handles = []
        for name in gpts:
            handles.append(subset[name].register_forward_hook(add_batch(name)))

        for j in range(args.nsamples):
            outs[j] = layer(inps[j].unsqueeze(0), attention_mask=attention_mask, position_embeddings=position_embeddings)[0]
        for h in handles:
            h.remove()

        for name in gpts:
            print(i, name)
            print('Pruning ...')

            gpts[name].fasterprune(args.sparsity_ratio, prune_n=prune_n, prune_m=prune_m, percdamp=0.01, blocksize=128)
            gpts[name].free()

        for j in range(args.nsamples):
            outs[j] = layer(inps[j].unsqueeze(0), attention_mask=None, position_embeddings=position_embeddings)[0]

        layers[i] = layer 
        setattr(model.config, "use_cache", use_cache)
        torch.cuda.empty_cache()

        inps, outs = outs, inps

    setattr(model.config, "use_cache", use_cache)
    torch.cuda.empty_cache()

def get_lm_layers(model):
    # For LLaVA and similar VLMs with language_model.model.layers structure
    if hasattr(model, "language_model") and hasattr(model.language_model, "model") and hasattr(model.language_model.model, "layers"):
        return model.language_model.model.layers
    # For Qwen2.5-VL and similar VLMs with language_model.layers structure
    elif hasattr(model, "language_model") and hasattr(model.language_model, "layers"):
        return model.language_model.layers
    # For standard LLMs
    elif hasattr(model, "model") and hasattr(model.model, "layers"):
        return model.model.layers
    else:
        raise AttributeError("Cannot find language model layers in the model.")