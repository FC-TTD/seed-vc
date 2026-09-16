"""Native SeedVCWrapper hooks without placement, precision or offload changes."""

import gc
import os
from .concurrency_v1 import enable_native_concurrency


def load_model():
    root = os.getenv("VC_ROOT", "/data/ttd/seed-vc/")
    os.environ["HF_HUB_CACHE"] = os.path.join(root, "checkpoints/hf_cache")
    from seed_vc_wrapper import SeedVCWrapper

    return enable_native_concurrency(SeedVCWrapper())


def final_audio(generator):
    result = None
    try:
        for _, audio in generator:
            if audio is not None:
                result = audio
    finally:
        generator.close()
    return result


def completion(model):
    import torch

    if torch.cuda.is_available():
        torch.cuda.synchronize()


def cleanup():
    import torch

    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()
