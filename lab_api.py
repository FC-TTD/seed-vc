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

import httpx
import soundfile as sf
import torch
import uvicorn
import yaml
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from hydra.utils import instantiate
from omegaconf import DictConfig
from ttd_fastapi_utils import SmartModel, apply_postprocess, setup_cuda_health


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("seed_vc_lab")

APP_ROOT = Path(__file__).resolve().parent
VC_ROOT = Path(os.environ.get("VC_ROOT", "/data/ttd/seed-vc"))
V1_UPSTREAM = os.environ.get("SEED_VC_V1_UPSTREAM", "http://svc-api").rstrip("/")
MODEL_TIMEOUT_SECONDS = int(os.environ.get("MODEL_TIMEOUT_SECONDS", "600"))
HTTP_TIMEOUT_SECONDS = float(os.environ.get("HTTP_TIMEOUT_SECONDS", "600"))

os.environ.setdefault("HF_HUB_CACHE", str(VC_ROOT / "checkpoints" / "hf_cache"))

v2_model_manager: SmartModel | None = None
inference_lock = threading.Lock()


def load_v2_model():
    if not torch.cuda.is_available():
        raise RuntimeError("Seed-VC V2 requires a CUDA GPU in this deployment")

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
    global v2_model_manager
    v2_model_manager = SmartModel(
        load_v2_model,
        timeout_seconds=MODEL_TIMEOUT_SECONDS,
        check_interval=30,
    )
    yield
    if v2_model_manager is not None:
        v2_model_manager.stop()
        v2_model_manager.unload()
    v2_model_manager = None


app = FastAPI(
    title="Seed-VC 双版本测试 API",
    version="2.0.0-preview",
    lifespan=lifespan,
)
setup_cuda_health(app)


def validate_audio(upload: UploadFile) -> None:
    content_type = (upload.content_type or "").lower()
    guessed_type, _ = mimetypes.guess_type(upload.filename or "")
    if content_type.startswith("audio/") or (guessed_type or "").startswith("audio/"):
        return
    raise HTTPException(status_code=400, detail=f"不支持的音频文件：{upload.filename or 'unnamed'}")


def write_upload(upload: UploadFile) -> str:
    suffix = Path(upload.filename or "audio.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as target:
        target.write(upload.file.read())
        return target.name


def wav_response(wave, sample_rate: int, *, post_process: bool, lufs: float,
                 trim_silence: bool, enable_eq: bool) -> Response:
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
        headers={"Content-Disposition": 'attachment; filename="seed-vc-output.wav"'},
    )


@app.get("/", include_in_schema=False)
@app.head("/", include_in_schema=False)
def home():
    return FileResponse(APP_ROOT / "static" / "index.html")


@app.get("/ui", include_in_schema=False)
def ui_redirect():
    return FileResponse(APP_ROOT / "static" / "index.html")


@app.get("/examples/source.wav", include_in_schema=False)
def example_source_audio():
    return FileResponse(APP_ROOT / "examples" / "source" / "source_s1.wav", media_type="audio/wav")


@app.get("/examples/reference.wav", include_in_schema=False)
def example_reference_audio():
    return FileResponse(APP_ROOT / "examples" / "reference" / "s1p1.wav", media_type="audio/wav")


@app.get("/api/models")
def model_catalog():
    return {
        "models": [
            {
                "version": "1.0",
                "mode": "proxy",
                "upstream": V1_UPSTREAM,
                "capabilities": ["voice-conversion", "singing-voice-conversion"],
            },
            {
                "version": "2.0",
                "mode": "local-lazy-load",
                "capabilities": ["voice-conversion", "style-emotion-accent-conversion", "anonymization"],
            },
        ]
    }


@app.post("/api/v1/convert")
def convert_v1(
    source_file: UploadFile = File(...),
    reference_file: UploadFile = File(...),
    diffusion_steps: int = Form(25, ge=1, le=200),
    length_adjust: float = Form(1.0, ge=0.5, le=2.0),
    inference_cfg_rate: float = Form(0.7, ge=0.0, le=1.0),
    f0_conditioned: bool = Form(False),
    auto_f0_adjust: bool = Form(False),
    pitch_shift: int = Form(0, ge=-24, le=24),
    post_process: bool = Form(True),
    lufs: float = Form(-23.0, ge=-40.0, le=-5.0),
    trim_silence: bool = Form(False),
    enable_eq: bool = Form(True),
):
    validate_audio(source_file)
    validate_audio(reference_file)
    files = {
        "src_file": (source_file.filename, source_file.file, source_file.content_type),
        "ref_file": (reference_file.filename, reference_file.file, reference_file.content_type),
    }
    data = {
        "steps": str(diffusion_steps),
        "length_adjust": str(length_adjust),
        "inference_cfg_rate": str(inference_cfg_rate),
        "f0_conditioned": str(f0_conditioned).lower(),
        "auto_f0_adjust": str(auto_f0_adjust).lower(),
        "pitch_shift": str(pitch_shift),
        "post_process": str(post_process).lower(),
        "lufs": str(lufs),
        "trim_silence": str(trim_silence).lower(),
        "enable_eq": str(enable_eq).lower(),
    }
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
            upstream = client.post(f"{V1_UPSTREAM}/svc_file", files=files, data=data)
    except httpx.HTTPError as exc:
        logger.exception("Seed-VC V1 upstream request failed")
        raise HTTPException(status_code=502, detail=f"Seed-VC v1 服务不可用：{exc}") from exc

    if upstream.status_code >= 400:
        detail = upstream.text[:1000]
        raise HTTPException(status_code=upstream.status_code, detail=detail)
    return Response(
        content=upstream.content,
        media_type=upstream.headers.get("content-type", "audio/wav"),
        headers={"Content-Disposition": 'attachment; filename="seed-vc-v1-output.wav"'},
    )


@app.post("/api/v2/convert")
def convert_v2(
    source_file: UploadFile = File(...),
    reference_file: UploadFile = File(...),
    diffusion_steps: int = Form(30, ge=1, le=200),
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
    validate_audio(source_file)
    validate_audio(reference_file)
    source_path = write_upload(source_file)
    reference_path = write_upload(reference_file)
    started_at = time.monotonic()

    try:
        if v2_model_manager is None:
            raise RuntimeError("Seed-VC V2 model manager is not ready")
        with inference_lock:
            model = v2_model_manager.get()
            final_audio = None
            generator = model.convert_voice_with_streaming(
                source_audio_path=source_path,
                target_audio_path=reference_path,
                diffusion_steps=diffusion_steps,
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
            raise RuntimeError("Seed-VC V2 did not produce a complete audio result")
        sample_rate, wave = final_audio
        logger.info("Seed-VC V2 conversion completed in %.2fs", time.monotonic() - started_at)
        return wav_response(
            wave,
            sample_rate,
            post_process=post_process,
            lufs=lufs,
            trim_silence=trim_silence,
            enable_eq=enable_eq,
        )
    except torch.cuda.OutOfMemoryError as exc:
        if v2_model_manager is not None:
            v2_model_manager.unload()
        raise HTTPException(status_code=503, detail="GPU 显存不足，V2 模型已卸载，请稍后重试") from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Seed-VC V2 conversion failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        for path in (source_path, reference_path):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass


@app.post("/api/v2/unload")
def unload_v2():
    if v2_model_manager is None:
        return JSONResponse({"status": "not-ready"}, status_code=503)
    with inference_lock:
        v2_model_manager.unload()
    return {"status": "unloaded"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7856)
