"""V2 API copied from api_v2.py; same Form parameters and native device policy."""
import io
import logging
import mimetypes
import os
import tempfile
import time
from pathlib import Path
import soundfile as sf
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from ttd_model_runtime.audio.postprocess import apply_postprocess
from ttd_model_runtime.protocol import HubError
from .adapter import final_audio as consume_audio
logger = logging.getLogger("svc_v2")


def build_api(runtime):
    app = FastAPI(title="SVC V2 API", version="2.0.0")
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
    @runtime.task
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
        import torch
        validate_audio(src_file)
        validate_audio(ref_file)
        paths = []
        started_at = time.monotonic()

        try:
            source_path = write_upload(src_file)
            paths.append(source_path)
            reference_path = write_upload(ref_file)
            paths.append(reference_path)
            model = runtime.get()
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
            final_audio = consume_audio(generator)

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
            raise HTTPException(status_code=503, detail="CUDA out of memory; residency is managed by Hub") from exc
        except (HTTPException, HubError):
            raise
        except Exception as exc:
            logger.exception("SVC V2 conversion failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            for path in paths:
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass


    @app.post("/unload")
    def unload():
        raise HTTPException(status_code=409, detail="Managed by Hub: use the operator service drain endpoint for svc-v2")

    return app
