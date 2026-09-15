"""Original api_v2.py loader; serialize native AR caches through generator completion."""
import os
import threading
from functools import wraps
from pathlib import Path
from .adapter import cleanup, completion


def load_model():
    os.environ.setdefault("HF_HUB_CACHE", str(Path(os.getenv("VC_ROOT", "/data/ttd/seed-vc")) / "checkpoints/hf_cache"))
    import torch
    import yaml
    from hydra.utils import instantiate
    from omegaconf import DictConfig
    APP_ROOT = Path(__file__).resolve().parents[1]
    if not torch.cuda.is_available():
        raise RuntimeError("SVC V2 requires CUDA")

    config_path = APP_ROOT / "configs" / "v2" / "vc_wrapper.yaml"
    config = DictConfig(yaml.safe_load(config_path.read_text(encoding="utf-8")))
    model = instantiate(config)
    model.load_checkpoints()
    device = torch.device("cuda")
    model.to(device)
    model.eval()
    model.setup_ar_caches(
        max_batch_size=1,
        max_seq_len=4096,
        dtype=torch.float16,
        device=device,
    )
    original = model.convert_voice_with_streaming
    lock = threading.RLock()
    @wraps(original)
    def convert(*args, **kwargs):
        with lock:
            yield from original(*args, **kwargs)
    model.convert_voice_with_streaming = convert
    return model


