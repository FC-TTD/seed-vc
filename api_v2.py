import io
import logging
import mimetypes
import os
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import soundfile as sf
import torch
import uvicorn
import yaml
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from hydra.utils import instantiate
from omegaconf import DictConfig
from ttd_fastapi_utils import SmartModel, apply_postprocess, setup_cuda_health


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("svc_v2")

APP_ROOT = Path(__file__).resolve().parent
VC_ROOT = Path(os.environ.get("VC_ROOT", "/data/ttd/seed-vc"))
MODEL_TIMEOUT_SECONDS = int(os.environ.get("MODEL_TIMEOUT_SECONDS", "7200"))
os.environ.setdefault("HF_HUB_CACHE", str(VC_ROOT / "checkpoints" / "hf_cache"))

model_manager: SmartModel | None = None
inference_lock = threading.Lock()


def load_model():
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
    return model


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    del app
    global model_manager
    model_manager = SmartModel(
        load_model,
        timeout_seconds=MODEL_TIMEOUT_SECONDS,
        check_interval=30,
    )
    yield
    if model_manager is not None:
        model_manager.stop()
        model_manager.unload()
    model_manager = None


app = FastAPI(title="SVC V2 API", version="2.0.0", lifespan=lifespan)
setup_cuda_health(app)


def validate_audio(upload: UploadFile) -> None:
    content_type = (upload.content_type or "").lower()
    guessed_type, _ = mimetypes.guess_type(upload.filename or "")
    if content_type.startswith("audio/") or (guessed_type or "").startswith("audio/"):
        return
    raise HTTPException(status_code=400, detail="Only audio files are accepted")


def write_upload(upload: UploadFile) -> str:
    suffix = Path(upload.filename or "audio.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as target:
        target.write(upload.file.read())
        return target.name


def wav_response(
    wave,
    sample_rate: int,
    *,
    post_process: bool,
    lufs: float,
    trim_silence: bool,
    enable_eq: bool,
) -> Response:
    processed = apply_postprocess(
        wave,
        sample_rate,
        target_loudness=float(lufs),
        enable=post_process,
        trim_silence=trim_silence,
        enable_eq=enable_eq,
    )
    output = io.BytesIO()
    sf.write(output, processed, sample_rate, format="WAV")
    return Response(
        content=output.getvalue(),
        media_type="audio/wav",
        headers={"Content-Disposition": 'attachment; filename="svc-v2-output.wav"'},
    )


@app.get("/capabilities")
def capabilities():
    return {
        "model": "svc-v2",
        "sample_rate": 22050,
        "lazy_load": True,
        "extensions": [
            "steps",
            "length_adjust",
            "intelligibility_cfg_rate",
            "similarity_cfg_rate",
            "top_p",
            "temperature",
            "repetition_penalty",
            "convert_style",
            "anonymization_only",
            "postprocess",
            "lufs",
            "trim_silence",
            "enable_eq",
        ],
    }


@app.post("/svc_file")
def svc_file(
    src_file: UploadFile = File(...),
    ref_file: UploadFile = File(...),
    steps: int = Form(30, ge=1, le=200),
    length_adjust: float = Form(1.0, ge=0.5, le=2.0),
    intelligibility_cfg_rate: float = Form(0.0, ge=0.0, le=1.0),
    similarity_cfg_rate: float = Form(0.7, ge=0.0, le=1.0),
    top_p: float = Form(0.9, ge=0.1, le=1.0),
    temperature: float = Form(1.0, ge=0.1, le=2.0),
    repetition_penalty: float = Form(1.0, ge=1.0, le=3.0),
    convert_style: bool = Form(False),
    anonymization_only: bool = Form(False),
    post_process: bool = Form(True),
    lufs: float = Form(-23.0, ge=-40.0, le=-5.0),
    trim_silence: bool = Form(False),
    enable_eq: bool = Form(True),
):
    validate_audio(src_file)
    validate_audio(ref_file)
    source_path = write_upload(src_file)
    reference_path = write_upload(ref_file)
    started_at = time.monotonic()

    try:
        if model_manager is None:
            raise RuntimeError("SVC V2 model manager is not ready")
        with inference_lock:
            model = model_manager.get()
            final_audio = None
            generator = model.convert_voice_with_streaming(
                source_audio_path=source_path,
                target_audio_path=reference_path,
                diffusion_steps=steps,
                length_adjust=length_adjust,
                intelligebility_cfg_rate=intelligibility_cfg_rate,
                similarity_cfg_rate=similarity_cfg_rate,
                top_p=top_p,
                temperature=temperature,
                repetition_penalty=repetition_penalty,
                convert_style=convert_style,
                anonymization_only=anonymization_only,
                device=torch.device("cuda"),
                dtype=torch.float16,
                stream_output=True,
            )
            for _, candidate in generator:
                if candidate is not None:
                    final_audio = candidate

        if final_audio is None:
            raise RuntimeError("SVC V2 did not produce a complete audio result")
        sample_rate, wave = final_audio
        logger.info("SVC V2 conversion completed in %.2fs", time.monotonic() - started_at)
        return wav_response(
            wave,
            sample_rate,
            post_process=post_process,
            lufs=lufs,
            trim_silence=trim_silence,
            enable_eq=enable_eq,
        )
    except torch.cuda.OutOfMemoryError as exc:
        if model_manager is not None:
            model_manager.unload()
        raise HTTPException(status_code=503, detail="CUDA out of memory; model unloaded") from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("SVC V2 conversion failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        for path in (source_path, reference_path):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass


@app.post("/unload")
def unload():
    if model_manager is None:
        return JSONResponse({"status": "not-ready"}, status_code=503)
    with inference_lock:
        model_manager.unload()
    return {"status": "unloaded"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7856)
