"""V1 UI copied from app.py at 0e21993; API defaults and Runtime hook only."""

import gradio as gr
import soundfile as sf
from .api import post_process_file


def build_ui(runtime):
    @runtime.task
    def convert_voice_v1_wrapper(
        source_audio_path,
        target_audio_path,
        diffusion_steps=50,
        length_adjust=1.0,
        inference_cfg_rate=0.7,
        f0_condition=False,
        auto_f0_adjust=False,
        pitch_shift=0,
    ):
        generator = runtime.get().convert_voice(
            source_audio_path,
            target_audio_path,
            int(diffusion_steps),
            float(length_adjust),
            float(inference_cfg_rate),
            bool(f0_condition),
            bool(auto_f0_adjust),
            int(pitch_shift),
        )
        try:
            for chunk, audio in generator:
                if audio is not None:
                    sr, wave = audio
                    processed, _ = sf.read(post_process_file(wave, sr), dtype="float32")
                    audio = (sr, processed)
                yield chunk, audio
        finally:
            generator.close()

    # Set up Gradio interface
    description = (
        "Zero-shot voice conversion with in-context learning. For local deployment please check [GitHub repository](https://github.com/Plachtaa/seed-vc) "
        "for details and updates.<br>Note that any reference audio will be forcefully clipped to 25s if beyond this length.<br> "
        "If total duration of source and reference audio exceeds 30s, source audio will be processed in chunks.<br> "
        "无需训练的 zero-shot 语音/歌声转换模型，若需本地部署查看[GitHub页面](https://github.com/Plachtaa/seed-vc)<br>"
        "请注意，参考音频若超过 25 秒，则会被自动裁剪至此长度。<br>若源音频和参考音频的总时长超过 30 秒，源音频将被分段处理。"
    )

    inputs = [
        gr.Audio(type="filepath", label="Source Audio / 源音频"),
        gr.Audio(type="filepath", label="Reference Audio / 参考音频"),
        gr.Slider(
            minimum=1,
            maximum=200,
            value=50,
            step=1,
            label="Diffusion Steps / 扩散步数",
            info="50 by API default, 50~100 for best quality / 默认为 50，50~100 为最佳质量",
        ),
        gr.Slider(
            minimum=0.5,
            maximum=2.0,
            step=0.1,
            value=1.0,
            label="Length Adjust / 长度调整",
            info="<1.0 for speed-up speech, >1.0 for slow-down speech / <1.0 加速语速，>1.0 减慢语速",
        ),
        gr.Slider(
            minimum=0.0,
            maximum=1.0,
            step=0.1,
            value=0.7,
            label="Inference CFG Rate",
            info="has subtle influence / 有微小影响",
        ),
        gr.Checkbox(
            label="Use F0 conditioned model / 启用F0输入",
            value=False,
            info="Must set to true for singing voice conversion / 歌声转换时必须勾选",
        ),
        gr.Checkbox(
            label="Auto F0 adjust / 自动F0调整",
            value=False,
            info="Roughly adjust F0 to match target voice. Only works when F0 conditioned model is used. / 粗略调整 F0 以匹配目标音色，仅在勾选 '启用F0输入' 时生效",
        ),
        gr.Slider(
            label="Pitch shift / 音调变换",
            minimum=-24,
            maximum=24,
            step=1,
            value=0,
            info="Pitch shift in semitones, only works when F0 conditioned model is used / 半音数的音高变换，仅在勾选 '启用F0输入' 时生效",
        ),
    ]

    examples = [
        [
            "examples/source/yae_0.wav",
            "examples/reference/dingzhen_0.wav",
            25,
            1.0,
            0.7,
            False,
            True,
            0,
        ],
        [
            "examples/source/jay_0.wav",
            "examples/reference/azuma_0.wav",
            25,
            1.0,
            0.7,
            True,
            True,
            0,
        ],
        [
            "examples/source/Wiz Khalifa,Charlie Puth - See You Again [vocals]_[cut_28sec].wav",
            "examples/reference/teio_0.wav",
            100,
            1.0,
            0.7,
            True,
            False,
            0,
        ],
        [
            "examples/source/TECHNOPOLIS - 2085 [vocals]_[cut_14sec].wav",
            "examples/reference/trump_0.wav",
            50,
            1.0,
            0.7,
            True,
            False,
            -12,
        ],
    ]

    outputs = [
        gr.Audio(label="Stream Output Audio / 流式输出", streaming=True, format="mp3"),
        gr.Audio(label="Full Output Audio / 完整输出", streaming=False, format="wav"),
    ]

    return gr.Interface(
        fn=convert_voice_v1_wrapper,
        api_name="convert_voice",
        description=description,
        inputs=inputs,
        outputs=outputs,
        title="Seed Voice Conversion V1 (Voice & Singing Voice Conversion)",
        examples=examples,
        cache_examples=False,
        concurrency_limit=None,
    )
