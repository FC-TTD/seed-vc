"""Preset-based postprocess API endpoints for seed-vc API.

This module provides standalone audio post-processing endpoints using
ttd_fastapi_utils.preset effects (telephone, smart_assistant, inner_monologue, radio, intercom).

Usage in main app:
    from postprocess_api import router as postprocess_router
    app.include_router(postprocess_router, prefix="/postprocess", tags=["postprocess"])
"""

import io
import inspect
import logging
from typing import Annotated, Literal

import numpy as np
import soundfile as sf
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ttd_fastapi_utils import postprocess
from ttd_fastapi_utils.preset import apply_preset, list_presets, preset_map, preset_metadata

logger = logging.getLogger(__name__)
router = APIRouter()


# ============== Pydantic Models ==============

class PresetListResponse(BaseModel):
    """Available presets response"""
    presets: list[str] = Field(description="Canonical preset names")
    aliases: dict[str, list[str]] = Field(description="Preset name aliases")
    descriptions: dict[str, str] = Field(description="Preset descriptions")
    metadata: dict[str, dict] = Field(description="Preset metadata from ttd_fastapi_utils")


class PresetBaseParams(BaseModel):
    """Base parameters common to all presets"""
    target_loudness: float = Field(default=-23.0, ge=-40.0, le=-10.0, description="Target LUFS loudness")
    trim_silence: bool = Field(default=False, description="Trim leading/trailing silence")
    enable_eq: bool = Field(default=True, description="Enable EQ in standard chain")
    enable_limiter: bool = Field(default=True, description="Enable final limiter")
    limiter_threshold: float = Field(default=0.98, ge=0.5, le=1.0, description="Limiter threshold (0-1)")


class TelephoneParams(PresetBaseParams):
    """Telephone preset parameters"""
    use_standard_chain: bool = Field(default=False, description="Apply standard postprocess chain first")
    enable_saturate: bool = Field(default=True, description="Enable saturation")
    enable_reverb: bool = Field(default=False, description="Enable reverb")
    low_cut_hz: float = Field(default=300.0, ge=50.0, le=1000.0, description="Bandpass low cutoff (Hz)")
    high_cut_hz: float = Field(default=3400.0, ge=2000.0, le=8000.0, description="Bandpass high cutoff (Hz)")
    wet_ratio: float = Field(default=0.95, ge=0.0, le=1.0, description="Wet signal mix ratio")
    drive: float = Field(default=1.45, ge=1.0, le=5.0, description="Saturation drive amount")
    reverb_room_size: float = Field(default=0.18, ge=0.0, le=1.0, description="Reverb room size")
    reverb_damping: float = Field(default=0.65, ge=0.0, le=1.0, description="Reverb damping")
    reverb_pre_delay_ms: float = Field(default=12.0, ge=0.0, le=200.0, description="Reverb pre-delay (ms)")
    reverb_wet: float = Field(default=0.0, ge=0.0, le=1.0, description="Reverb wet amount")

    @classmethod
    def as_form(
        cls,
        target_loudness: float = Form(default=-23.0, ge=-40.0, le=-10.0),
        trim_silence: bool = Form(default=False),
        enable_eq: bool = Form(default=True),
        enable_limiter: bool = Form(default=True),
        limiter_threshold: float = Form(default=0.98, ge=0.5, le=1.0),
        use_standard_chain: bool = Form(default=False),
        enable_saturate: bool = Form(default=True),
        enable_reverb: bool = Form(default=False),
        low_cut_hz: float = Form(default=300.0, ge=50.0, le=1000.0),
        high_cut_hz: float = Form(default=3400.0, ge=2000.0, le=8000.0),
        wet_ratio: float = Form(default=0.95, ge=0.0, le=1.0),
        drive: float = Form(default=1.45, ge=1.0, le=5.0),
        reverb_room_size: float = Form(default=0.18, ge=0.0, le=1.0),
        reverb_damping: float = Form(default=0.65, ge=0.0, le=1.0),
        reverb_pre_delay_ms: float = Form(default=12.0, ge=0.0, le=200.0),
        reverb_wet: float = Form(default=0.0, ge=0.0, le=1.0),
    ) -> "TelephoneParams":
        return cls(
            target_loudness=target_loudness,
            trim_silence=trim_silence,
            enable_eq=enable_eq,
            enable_limiter=enable_limiter,
            limiter_threshold=limiter_threshold,
            use_standard_chain=use_standard_chain,
            enable_saturate=enable_saturate,
            enable_reverb=enable_reverb,
            low_cut_hz=low_cut_hz,
            high_cut_hz=high_cut_hz,
            wet_ratio=wet_ratio,
            drive=drive,
            reverb_room_size=reverb_room_size,
            reverb_damping=reverb_damping,
            reverb_pre_delay_ms=reverb_pre_delay_ms,
            reverb_wet=reverb_wet,
        )


class SmartAssistantParams(PresetBaseParams):
    """Smart assistant preset parameters"""
    use_standard_chain: bool = Field(default=True, description="Apply standard postprocess chain first")
    enable_saturate: bool = Field(default=True, description="Enable saturation")
    enable_delay: bool = Field(default=True, description="Enable delay effect")
    enable_reverb: bool = Field(default=False, description="Enable reverb")
    low_cut_hz: float = Field(default=170.0, ge=50.0, le=500.0, description="Bandpass low cutoff (Hz)")
    high_cut_hz: float = Field(default=4250.0, ge=2000.0, le=8000.0, description="Bandpass high cutoff (Hz)")
    drive: float = Field(default=1.45, ge=1.0, le=5.0, description="Saturation drive amount")
    saturate_wet: float = Field(default=0.06, ge=0.0, le=1.0, description="Saturation wet ratio")
    wet_ratio: float = Field(default=0.95, ge=0.0, le=1.0, description="Overall wet mix ratio")
    delay_ms: float = Field(default=41.0, ge=1.0, le=200.0, description="Delay time (ms)")
    decay: float = Field(default=0.53, ge=0.0, le=1.0, description="Delay decay factor")
    repeats: int = Field(default=2, ge=0, le=10, description="Delay repeats")
    delay_wet: float = Field(default=0.70, ge=0.0, le=1.0, description="Delay wet ratio")
    reverb_room_size: float = Field(default=0.32, ge=0.0, le=1.0, description="Reverb room size")
    reverb_damping: float = Field(default=0.42, ge=0.0, le=1.0, description="Reverb damping")
    reverb_pre_delay_ms: float = Field(default=16.0, ge=0.0, le=200.0, description="Reverb pre-delay (ms)")
    reverb_wet: float = Field(default=0.0, ge=0.0, le=1.0, description="Reverb wet amount")

    @classmethod
    def as_form(
        cls,
        target_loudness: float = Form(default=-23.0, ge=-40.0, le=-10.0),
        trim_silence: bool = Form(default=False),
        enable_eq: bool = Form(default=True),
        enable_limiter: bool = Form(default=True),
        limiter_threshold: float = Form(default=0.98, ge=0.5, le=1.0),
        use_standard_chain: bool = Form(default=True),
        enable_saturate: bool = Form(default=True),
        enable_delay: bool = Form(default=True),
        enable_reverb: bool = Form(default=False),
        low_cut_hz: float = Form(default=170.0, ge=50.0, le=500.0),
        high_cut_hz: float = Form(default=4250.0, ge=2000.0, le=8000.0),
        drive: float = Form(default=1.45, ge=1.0, le=5.0),
        saturate_wet: float = Form(default=0.06, ge=0.0, le=1.0),
        wet_ratio: float = Form(default=0.95, ge=0.0, le=1.0),
        delay_ms: float = Form(default=41.0, ge=1.0, le=200.0),
        decay: float = Form(default=0.53, ge=0.0, le=1.0),
        repeats: int = Form(default=2, ge=0, le=10),
        delay_wet: float = Form(default=0.70, ge=0.0, le=1.0),
        reverb_room_size: float = Form(default=0.32, ge=0.0, le=1.0),
        reverb_damping: float = Form(default=0.42, ge=0.0, le=1.0),
        reverb_pre_delay_ms: float = Form(default=16.0, ge=0.0, le=200.0),
        reverb_wet: float = Form(default=0.0, ge=0.0, le=1.0),
    ) -> "SmartAssistantParams":
        return cls(
            target_loudness=target_loudness,
            trim_silence=trim_silence,
            enable_eq=enable_eq,
            enable_limiter=enable_limiter,
            limiter_threshold=limiter_threshold,
            use_standard_chain=use_standard_chain,
            enable_saturate=enable_saturate,
            enable_delay=enable_delay,
            enable_reverb=enable_reverb,
            low_cut_hz=low_cut_hz,
            high_cut_hz=high_cut_hz,
            drive=drive,
            saturate_wet=saturate_wet,
            wet_ratio=wet_ratio,
            delay_ms=delay_ms,
            decay=decay,
            repeats=repeats,
            delay_wet=delay_wet,
            reverb_room_size=reverb_room_size,
            reverb_damping=reverb_damping,
            reverb_pre_delay_ms=reverb_pre_delay_ms,
            reverb_wet=reverb_wet,
        )


class InnerMonologueParams(PresetBaseParams):
    """Inner monologue preset parameters"""
    use_standard_chain: bool = Field(default=False, description="Apply standard postprocess chain first")
    enable_eq: bool = Field(default=False, description="Enable EQ in standard chain")
    enable_delay: bool = Field(default=True, description="Enable delay effect")
    enable_reverb: bool = Field(default=True, description="Enable reverb")
    lowpass_hz: float = Field(default=2200.0, ge=500.0, le=4000.0, description="Lowpass cutoff (Hz)")
    delay_ms: float = Field(default=85.0, ge=1.0, le=200.0, description="Delay time (ms)")
    decay: float = Field(default=0.45, ge=0.0, le=1.0, description="Delay decay factor")
    repeats: int = Field(default=2, ge=0, le=10, description="Delay repeats")
    wet_ratio: float = Field(default=0.28, ge=0.0, le=1.0, description="Wet signal mix ratio")
    reverb_room_size: float = Field(default=1.0, ge=0.0, le=1.0, description="Reverb room size")
    reverb_damping: float = Field(default=0.0, ge=0.0, le=1.0, description="Reverb damping")
    reverb_pre_delay_ms: float = Field(default=120.0, ge=0.0, le=500.0, description="Reverb pre-delay (ms)")
    reverb_wet: float = Field(default=0.30, ge=0.0, le=1.0, description="Reverb wet amount")

    @classmethod
    def as_form(
        cls,
        target_loudness: float = Form(default=-23.0, ge=-40.0, le=-10.0),
        trim_silence: bool = Form(default=False),
        enable_eq: bool = Form(default=False),
        enable_limiter: bool = Form(default=True),
        limiter_threshold: float = Form(default=0.98, ge=0.5, le=1.0),
        use_standard_chain: bool = Form(default=False),
        enable_delay: bool = Form(default=True),
        enable_reverb: bool = Form(default=True),
        lowpass_hz: float = Form(default=2200.0, ge=500.0, le=4000.0),
        delay_ms: float = Form(default=85.0, ge=1.0, le=200.0),
        decay: float = Form(default=0.45, ge=0.0, le=1.0),
        repeats: int = Form(default=2, ge=0, le=10),
        wet_ratio: float = Form(default=0.28, ge=0.0, le=1.0),
        reverb_room_size: float = Form(default=1.0, ge=0.0, le=1.0),
        reverb_damping: float = Form(default=0.0, ge=0.0, le=1.0),
        reverb_pre_delay_ms: float = Form(default=120.0, ge=0.0, le=500.0),
        reverb_wet: float = Form(default=0.30, ge=0.0, le=1.0),
    ) -> "InnerMonologueParams":
        return cls(
            target_loudness=target_loudness,
            trim_silence=trim_silence,
            enable_eq=enable_eq,
            enable_limiter=enable_limiter,
            limiter_threshold=limiter_threshold,
            use_standard_chain=use_standard_chain,
            enable_delay=enable_delay,
            enable_reverb=enable_reverb,
            lowpass_hz=lowpass_hz,
            delay_ms=delay_ms,
            decay=decay,
            repeats=repeats,
            wet_ratio=wet_ratio,
            reverb_room_size=reverb_room_size,
            reverb_damping=reverb_damping,
            reverb_pre_delay_ms=reverb_pre_delay_ms,
            reverb_wet=reverb_wet,
        )


class RadioParams(PresetBaseParams):
    """Radio preset parameters"""
    use_standard_chain: bool = Field(default=False, description="Apply standard postprocess chain first")
    enable_eq: bool = Field(default=False, description="Enable EQ in standard chain")
    limiter_threshold: float = Field(default=0.9, ge=0.5, le=1.0, description="Limiter threshold (0-1)")
    enable_saturate: bool = Field(default=True, description="Enable saturation")
    enable_delay: bool = Field(default=True, description="Enable delay effect")
    enable_reverb: bool = Field(default=True, description="Enable reverb")
    low_cut_hz: float = Field(default=280.0, ge=50.0, le=1000.0, description="Bandpass low cutoff (Hz)")
    high_cut_hz: float = Field(default=1800.0, ge=1000.0, le=4000.0, description="Bandpass high cutoff (Hz)")
    drive: float = Field(default=2.8, ge=1.0, le=5.0, description="Saturation drive amount")
    delay_ms: float = Field(default=84.0, ge=1.0, le=200.0, description="Delay time (ms)")
    decay: float = Field(default=0.5, ge=0.0, le=1.0, description="Delay decay factor")
    repeats: int = Field(default=0, ge=0, le=10, description="Delay repeats")
    wet_ratio: float = Field(default=0.93, ge=0.0, le=1.0, description="Overall wet mix ratio")
    reverb_room_size: float = Field(default=0.28, ge=0.0, le=1.0, description="Reverb room size")
    reverb_damping: float = Field(default=0.58, ge=0.0, le=1.0, description="Reverb damping")
    reverb_pre_delay_ms: float = Field(default=14.0, ge=0.0, le=200.0, description="Reverb pre-delay (ms)")
    reverb_wet: float = Field(default=0.08, ge=0.0, le=1.0, description="Reverb wet amount")

    @classmethod
    def as_form(
        cls,
        target_loudness: float = Form(default=-23.0, ge=-40.0, le=-10.0),
        trim_silence: bool = Form(default=False),
        enable_eq: bool = Form(default=False),
        enable_limiter: bool = Form(default=True),
        limiter_threshold: float = Form(default=0.9, ge=0.5, le=1.0),
        use_standard_chain: bool = Form(default=False),
        enable_saturate: bool = Form(default=True),
        enable_delay: bool = Form(default=True),
        enable_reverb: bool = Form(default=True),
        low_cut_hz: float = Form(default=280.0, ge=50.0, le=1000.0),
        high_cut_hz: float = Form(default=1800.0, ge=1000.0, le=4000.0),
        drive: float = Form(default=2.8, ge=1.0, le=5.0),
        delay_ms: float = Form(default=84.0, ge=1.0, le=200.0),
        decay: float = Form(default=0.5, ge=0.0, le=1.0),
        repeats: int = Form(default=0, ge=0, le=10),
        wet_ratio: float = Form(default=0.93, ge=0.0, le=1.0),
        reverb_room_size: float = Form(default=0.28, ge=0.0, le=1.0),
        reverb_damping: float = Form(default=0.58, ge=0.0, le=1.0),
        reverb_pre_delay_ms: float = Form(default=14.0, ge=0.0, le=200.0),
        reverb_wet: float = Form(default=0.08, ge=0.0, le=1.0),
    ) -> "RadioParams":
        return cls(
            target_loudness=target_loudness,
            trim_silence=trim_silence,
            enable_eq=enable_eq,
            enable_limiter=enable_limiter,
            limiter_threshold=limiter_threshold,
            use_standard_chain=use_standard_chain,
            enable_saturate=enable_saturate,
            enable_delay=enable_delay,
            enable_reverb=enable_reverb,
            low_cut_hz=low_cut_hz,
            high_cut_hz=high_cut_hz,
            drive=drive,
            delay_ms=delay_ms,
            decay=decay,
            repeats=repeats,
            wet_ratio=wet_ratio,
            reverb_room_size=reverb_room_size,
            reverb_damping=reverb_damping,
            reverb_pre_delay_ms=reverb_pre_delay_ms,
            reverb_wet=reverb_wet,
        )


class IntercomParams(PresetBaseParams):
    """Intercom preset parameters"""
    use_standard_chain: bool = Field(default=False, description="Apply standard postprocess chain first")
    enable_eq: bool = Field(default=False, description="Enable EQ in standard chain")
    limiter_threshold: float = Field(default=0.97, ge=0.5, le=1.0, description="Limiter threshold (0-1)")
    enable_saturate: bool = Field(default=True, description="Enable saturation")
    enable_reverb: bool = Field(default=False, description="Enable reverb")
    low_cut_hz: float = Field(default=450.0, ge=100.0, le=1000.0, description="Bandpass low cutoff (Hz)")
    high_cut_hz: float = Field(default=2800.0, ge=1500.0, le=4000.0, description="Bandpass high cutoff (Hz)")
    drive: float = Field(default=1.75, ge=1.0, le=5.0, description="Saturation drive amount")
    wet_ratio: float = Field(default=1.0, ge=0.0, le=1.0, description="Overall wet mix ratio")
    reverb_room_size: float = Field(default=0.16, ge=0.0, le=1.0, description="Reverb room size")
    reverb_damping: float = Field(default=0.72, ge=0.0, le=1.0, description="Reverb damping")
    reverb_pre_delay_ms: float = Field(default=10.0, ge=0.0, le=100.0, description="Reverb pre-delay (ms)")
    reverb_wet: float = Field(default=0.0, ge=0.0, le=1.0, description="Reverb wet amount")

    @classmethod
    def as_form(
        cls,
        target_loudness: float = Form(default=-23.0, ge=-40.0, le=-10.0),
        trim_silence: bool = Form(default=False),
        enable_eq: bool = Form(default=False),
        enable_limiter: bool = Form(default=True),
        limiter_threshold: float = Form(default=0.97, ge=0.5, le=1.0),
        use_standard_chain: bool = Form(default=False),
        enable_saturate: bool = Form(default=True),
        enable_reverb: bool = Form(default=False),
        low_cut_hz: float = Form(default=450.0, ge=100.0, le=1000.0),
        high_cut_hz: float = Form(default=2800.0, ge=1500.0, le=4000.0),
        drive: float = Form(default=1.75, ge=1.0, le=5.0),
        wet_ratio: float = Form(default=1.0, ge=0.0, le=1.0),
        reverb_room_size: float = Form(default=0.16, ge=0.0, le=1.0),
        reverb_damping: float = Form(default=0.72, ge=0.0, le=1.0),
        reverb_pre_delay_ms: float = Form(default=10.0, ge=0.0, le=100.0),
        reverb_wet: float = Form(default=0.0, ge=0.0, le=1.0),
    ) -> "IntercomParams":
        return cls(
            target_loudness=target_loudness,
            trim_silence=trim_silence,
            enable_eq=enable_eq,
            enable_limiter=enable_limiter,
            limiter_threshold=limiter_threshold,
            use_standard_chain=use_standard_chain,
            enable_saturate=enable_saturate,
            enable_reverb=enable_reverb,
            low_cut_hz=low_cut_hz,
            high_cut_hz=high_cut_hz,
            drive=drive,
            wet_ratio=wet_ratio,
            reverb_room_size=reverb_room_size,
            reverb_damping=reverb_damping,
            reverb_pre_delay_ms=reverb_pre_delay_ms,
            reverb_wet=reverb_wet,
        )


# Map preset names to their parameter models
PRESET_PARAMS_MAP: dict[str, type[PresetBaseParams]] = {
    "telephone": TelephoneParams,
    "smart_assistant": SmartAssistantParams,
    "inner_monologue": InnerMonologueParams,
    "radio": RadioParams,
    "intercom": IntercomParams,
}
PRESET_FUNCTION_MAP = {
    preset_name: preset_map()[preset_name]
    for preset_name in list_presets()
}

# ============== Helper Functions ==============


def _load_audio(file: UploadFile) -> tuple[np.ndarray, int]:
    """Load audio from uploaded file."""
    contents = file.file.read()
    with io.BytesIO(contents) as buf:
        wav, sr = sf.read(buf)
    return wav, sr


def _validate_audio_file(file: UploadFile) -> None:
    """Validate uploaded audio file metadata."""
    mime_type = file.content_type or ""
    filename = file.filename or ""
    if not (mime_type.startswith("audio/") or filename.lower().endswith((".wav", ".mp3", ".flac", ".ogg"))):
        raise HTTPException(status_code=400, detail="Invalid file type. Only audio files are accepted.")


def _save_audio(wav: np.ndarray, sr: int) -> io.BytesIO:
    """Save audio to BytesIO buffer."""
    output = io.BytesIO()
    sf.write(output, wav, sr, format="WAV")
    output.seek(0)
    return output


def _apply_preset_with_params(
    preset_name: str,
    wav: np.ndarray,
    sr: int,
    params: dict,
) -> np.ndarray:
    """Apply preset with given parameters."""
    try:
        return apply_preset(preset_name, wav, sr, **params)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Preset application failed: %s", preset_name)
        raise HTTPException(status_code=500, detail=f"Audio processing failed: {str(e)}")


def _apply_standard_chain_if_needed(
    wav: np.ndarray,
    sr: int,
    params: PresetBaseParams,
) -> np.ndarray:
    """Preserve wrapper-level standard-chain compatibility across preset API changes."""
    if not getattr(params, "use_standard_chain", False):
        return wav
    return postprocess.apply_postprocess(
        wav,
        sr,
        target_loudness=params.target_loudness,
        enable=True,
        trim_silence=params.trim_silence,
        enable_eq=params.enable_eq,
    )


def _extract_preset_kwargs(
    preset_name: str,
    params: PresetBaseParams,
) -> dict:
    """Filter wrapper params down to the kwargs accepted by the current preset function."""
    accepted = set(inspect.signature(PRESET_FUNCTION_MAP[preset_name]).parameters) - {"wav_data", "sr"}
    return {
        key: value
        for key, value in params.model_dump().items()
        if key in accepted
    }


def _process_preset_request(
    preset_name: str,
    file: UploadFile,
    params: PresetBaseParams,
) -> StreamingResponse:
    """Validate, load, process, and return audio for a preset request."""
    _validate_audio_file(file)
    try:
        wav, sr = _load_audio(file)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to load audio: {str(e)}")

    prepared = _apply_standard_chain_if_needed(wav, sr, params)
    preset_kwargs = _extract_preset_kwargs(preset_name, params)
    processed = _apply_preset_with_params(preset_name, prepared, sr, preset_kwargs)
    output = _save_audio(processed, sr)
    return StreamingResponse(
        output,
        media_type="audio/wav",
        headers={"Content-Disposition": f"attachment; filename={preset_name}_output.wav"},
    )


# ============== API Endpoints ==============


@router.get("/presets", response_model=PresetListResponse)
async def get_presets():
    """Get list of available post-processing presets with descriptions."""
    metadata = preset_metadata()
    descriptions = {
        preset_name: str(metadata.get(preset_name, {}).get("summary", preset_name))
        for preset_name in list_presets()
    }

    aliases = {
        "telephone": ["phone", "电话"],
        "smart_assistant": ["assistant", "智能语音"],
        "inner_monologue": ["inner_voice", "心声独白", "心声"],
        "radio": ["广播"],
        "intercom": ["对讲机"],
    }

    return PresetListResponse(
        presets=list(list_presets()),
        aliases=aliases,
        descriptions=descriptions,
        metadata=metadata,
    )


@router.post("/apply/telephone")
async def apply_telephone(
    params: Annotated[TelephoneParams, Depends(TelephoneParams.as_form)],
    file: UploadFile = File(...),
):
    """Apply telephone preset (narrow band 300-3400Hz + light saturation)."""
    return _process_preset_request("telephone", file, params)


@router.post("/apply/smart_assistant")
async def apply_smart_assistant(
    params: Annotated[SmartAssistantParams, Depends(SmartAssistantParams.as_form)],
    file: UploadFile = File(...),
):
    """Apply smart assistant preset (standard chain + bandpass + light saturation + spatial tail)."""
    return _process_preset_request("smart_assistant", file, params)


@router.post("/apply/inner_monologue")
async def apply_inner_monologue(
    params: Annotated[InnerMonologueParams, Depends(InnerMonologueParams.as_form)],
    file: UploadFile = File(...),
):
    """Apply inner monologue preset (soft lowpass + short echo + airy feel)."""
    return _process_preset_request("inner_monologue", file, params)


@router.post("/apply/radio")
async def apply_radio(
    params: Annotated[RadioParams, Depends(RadioParams.as_form)],
    file: UploadFile = File(...),
):
    """Apply radio preset (mid-range emphasis + cabinet/box spatial feel)."""
    return _process_preset_request("radio", file, params)


@router.post("/apply/intercom")
async def apply_intercom(
    params: Annotated[IntercomParams, Depends(IntercomParams.as_form)],
    file: UploadFile = File(...),
):
    """Apply intercom preset (narrower, harder mid-range texture)."""
    return _process_preset_request("intercom", file, params)


@router.post("/apply-generic/{preset_name}")
async def apply_preset_endpoint(
    preset_name: Literal["telephone", "smart_assistant", "inner_monologue", "radio", "intercom"],
    file: UploadFile = File(..., description="Input audio file (WAV/MP3/FLAC/OGG)"),
    target_loudness: float = Form(default=-23.0, ge=-40.0, le=-10.0),
    trim_silence: bool = Form(default=False),
    enable_eq: bool | None = Form(default=None),
    enable_limiter: bool = Form(default=True),
    limiter_threshold: float | None = Form(default=None, ge=0.5, le=1.0),
    use_standard_chain: bool | None = Form(default=None),
    enable_saturate: bool | None = Form(default=None),
    enable_delay: bool | None = Form(default=None),
    enable_reverb: bool | None = Form(default=None),
    low_cut_hz: float | None = Form(default=None),
    high_cut_hz: float | None = Form(default=None),
    lowpass_hz: float | None = Form(default=None),
    wet_ratio: float | None = Form(default=None),
    drive: float | None = Form(default=None),
    saturate_wet: float | None = Form(default=None),
    delay_ms: float | None = Form(default=None),
    decay: float | None = Form(default=None),
    repeats: int | None = Form(default=None),
    delay_wet: float | None = Form(default=None),
    reverb_room_size: float | None = Form(default=None),
    reverb_damping: float | None = Form(default=None),
    reverb_pre_delay_ms: float | None = Form(default=None),
    reverb_wet: float | None = Form(default=None),
):
    """
    Apply a post-processing preset to an audio file using form fields only.

    Preset-specific fields that are irrelevant to the selected preset are ignored.
    """
    raw_params = {
        "target_loudness": target_loudness,
        "trim_silence": trim_silence,
        "enable_eq": enable_eq,
        "enable_limiter": enable_limiter,
        "limiter_threshold": limiter_threshold,
        "use_standard_chain": use_standard_chain,
        "enable_saturate": enable_saturate,
        "enable_delay": enable_delay,
        "enable_reverb": enable_reverb,
        "low_cut_hz": low_cut_hz,
        "high_cut_hz": high_cut_hz,
        "lowpass_hz": lowpass_hz,
        "wet_ratio": wet_ratio,
        "drive": drive,
        "saturate_wet": saturate_wet,
        "delay_ms": delay_ms,
        "decay": decay,
        "repeats": repeats,
        "delay_wet": delay_wet,
        "reverb_room_size": reverb_room_size,
        "reverb_damping": reverb_damping,
        "reverb_pre_delay_ms": reverb_pre_delay_ms,
        "reverb_wet": reverb_wet,
    }
    model_cls = PRESET_PARAMS_MAP[preset_name]
    filtered_params = {
        key: value for key, value in raw_params.items()
        if key in model_cls.model_fields and value is not None
    }
    params = model_cls(**filtered_params)
    return _process_preset_request(preset_name, file, params)
