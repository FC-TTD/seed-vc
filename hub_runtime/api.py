"""API copied from api2.py at 0e21993; preserve query/form names and defaults."""

import io
import logging
import mimetypes
import os
import tempfile
import time
import soundfile as sf
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from ttd_model_runtime.protocol import HubError
from ttd_model_runtime.audio.postprocess import apply_postprocess
from .postprocess_api import router as postprocess_router
from .adapter import final_audio

logger = logging.getLogger(__name__)
VC_ROOT = os.environ.get("VC_ROOT", "/data/ttd/seed-vc/")


def post_process_file(
    vc_wave,
    sr: int,
    *,
    enable: bool = True,
    lufs: float = -23.0,
    trim_silence: bool = False,
    enable_eq: bool = True,
):
    output_file = io.BytesIO()
    np_wave = vc_wave
    try:
        np_wave = apply_postprocess(
            vc_wave,
            sr,
            target_loudness=float(lufs),
            enable=enable,
            trim_silence=trim_silence,
            enable_eq=enable_eq,
        )
        if enable:
            logger.info(
                "Standard postprocess applied. LUFS=%.2f trim_silence=%s enable_eq=%s",
                float(lufs),
                trim_silence,
                enable_eq,
            )
        else:
            logger.info("Standard postprocess skipped.")
    except Exception as e:
        logger.warning(f"Post-processing failed with error: {e}")
    sf.write(output_file, np_wave, sr, format="wav")
    output_file.seek(0)
    return output_file


def get_svc_voice(actor, voice):
    vc_ref_file = os.path.join(VC_ROOT, "models", actor, "train", voice + ".wav")
    if not os.path.exists(vc_ref_file):
        raise FileNotFoundError(f"Voice file not found: {vc_ref_file}")
    return vc_ref_file


def build_api(runtime):
    app = FastAPI(title="SVC V1 API", version="1.0.0")
    app.include_router(postprocess_router, prefix="/postprocess", tags=["postprocess"])

    @app.post("/infer_vc")
    @runtime.task
    def infer_vc(
        actor: str,
        voice: str,
        steps: str = "50",
        post_process: bool = True,
        lufs: float = Form(-23.0),
        trim_silence: bool = Form(False),
        enable_eq: bool = Form(True),
        file: UploadFile = File(...),
    ):
        time_start = time.time()
        # 检查文件类型
        mime_type, _ = mimetypes.guess_type(file.filename)
        if mime_type is None or not mime_type.startswith("audio/"):
            raise HTTPException(
                status_code=400,
                detail="Invalid file type. Only audio files are accepted.",
            )

        # 创建临时文件
        try:
            with tempfile.NamedTemporaryFile(delete=True, suffix=".wav") as temp_file:
                contents = file.file.read()
                temp_file.write(contents)
                temp_file.flush()  # 确保数据写入文件
                logger.info(
                    "recieved vc request. actor: %s, size: %d", actor, len(contents)
                )

                # Get model instance
                vc_wrapper = runtime.get()

                # 调用 vc 方法
                vc_generator = vc_wrapper.convert_voice(
                    temp_file.name,
                    get_svc_voice(actor, voice),
                    int(steps),
                    1.0,
                    0.7,
                    False,
                    False,
                    0,
                )
                # 获取生成器的最终结果
                final_result = final_audio(vc_generator)
                if not final_result:
                    raise HTTPException(status_code=500, detail="处理音频失败")
                sr, np_wav = final_result

                logger.info("vc done. cost time: %.03f", time.time() - time_start)

        except FileNotFoundError as e:
            logger.exception("svc task failed: ")
            raise HTTPException(status_code=404, detail=str(e))
        except HubError:
            raise
        except Exception as e:
            logger.exception("svc task failed: ")
            raise HTTPException(status_code=500, detail=str(e))
        if time.time() - time_start > 5:
            logger.warning(
                "svc task %s long time cost: %.03f", actor, time.time() - time_start
            )
        # 返回结果
        return StreamingResponse(
            post_process_file(
                np_wav,
                sr,
                enable=post_process,
                lufs=float(lufs),
                trim_silence=trim_silence,
                enable_eq=enable_eq,
            ),
            media_type="audio/wav",
            headers={"Content-Disposition": "attachment; filename=output.wav"},
        )

    @app.post("/svc_file")
    @runtime.task
    def svc_file(
        src_file: UploadFile = File(...),
        ref_file: UploadFile = File(...),
        steps: str = "50",
        length_adjust: str = "1.0",
        inference_cfg_rate: str = "0.7",
        f0_conditioned: bool = False,
        auto_f0_adjust: bool = False,
        pitch_shift: str = "0",
        post_process: bool = Form(True),
        lufs: float = Form(-23.0),
        trim_silence: bool = Form(False),
        enable_eq: bool = Form(True),
    ):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_file_src:
            contents = src_file.file.read()
            temp_file_src.write(contents)
            temp_file_src.flush()  # 确保数据写入文件

        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_file_ref:
            contents = ref_file.file.read()
            temp_file_ref.write(contents)
            temp_file_ref.flush()  # 确保数据写入文件

        try:
            # Get model instance
            vc_wrapper = runtime.get()

            # 处理上传的文件
            vc_generator = vc_wrapper.convert_voice(
                temp_file_src.name,
                temp_file_ref.name,
                diffusion_steps=int(steps),
                length_adjust=float(length_adjust),
                inference_cfg_rate=float(inference_cfg_rate),
                f0_condition=f0_conditioned,
                auto_f0_adjust=auto_f0_adjust,
                pitch_shift=int(pitch_shift),
            )

            # 获取生成器的最终结果
            final_result = final_audio(vc_generator)
            if not final_result:
                raise HTTPException(status_code=500, detail="处理音频失败")
            sr, np_wav = final_result
            return StreamingResponse(
                post_process_file(
                    np_wav,
                    sr,
                    enable=post_process,
                    lufs=float(lufs),
                    trim_silence=trim_silence,
                    enable_eq=enable_eq,
                ),
                media_type="audio/wav",
                headers={"Content-Disposition": "attachment; filename=output.wav"},
            )

        except HubError:
            raise
        except Exception as e:
            logger.exception("svc task failed: ")
            raise HTTPException(status_code=500, detail=str(e))

        finally:
            os.remove(temp_file_src.name)
            os.remove(temp_file_ref.name)

    return app
