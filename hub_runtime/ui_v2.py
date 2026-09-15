"""Native app.py V2 interface, using the managed instance and API postprocessing."""
import gradio as gr
from ttd_model_runtime.audio.postprocess import apply_postprocess

def build_ui(runtime):
    @runtime.task
    def convert_voice_v2_wrapper(source_audio_path, target_audio_path, diffusion_steps=30,
                                 length_adjust=1.0, intelligibility_cfg_rate=0.0,
                                 similarity_cfg_rate=0.7, top_p=0.9, temperature=1.0,
                                 repetition_penalty=1.0, convert_style=False, anonymization_only=False):
        import torch
        generator = runtime.get().convert_voice_with_streaming(
            source_audio_path=source_audio_path, target_audio_path=target_audio_path,
            diffusion_steps=int(diffusion_steps), length_adjust=float(length_adjust),
            intelligebility_cfg_rate=float(intelligibility_cfg_rate),
            similarity_cfg_rate=float(similarity_cfg_rate), top_p=float(top_p),
            temperature=float(temperature), repetition_penalty=float(repetition_penalty),
            convert_style=bool(convert_style), anonymization_only=bool(anonymization_only),
            device=torch.device("cuda"), dtype=torch.float16, stream_output=True,
        )
        try:
            for chunk, audio in generator:
                if audio is not None:
                    sr, wave = audio
                    audio = (sr, apply_postprocess(wave, sr, target_loudness=-23.0,
                             enable=True, trim_silence=False, enable_eq=True))
                yield chunk, audio
        finally:
            generator.close()

    # Set up Gradio interface
    description = (
        "Zero-shot voice/style conversion with in-context learning. For local deployment please check [GitHub repository](https://github.com/Plachtaa/seed-vc) "
        "for details and updates.<br>Note that any reference audio will be forcefully clipped to 25s if beyond this length.<br> "
        "If total duration of source and reference audio exceeds 30s, source audio will be processed in chunks.<br> "
        "Please click the 'convert style/emotion/accent' checkbox to convert the style, emotion, or accent of the source audio, or else only timbre conversion will be performed.<br> "
        "Click the 'anonymization only' checkbox will ignore reference audio but convert source to an 'average voice' determined by model itself.<br> "
        "无需训练的 zero-shot 语音/口音转换模型，若需本地部署查看[GitHub页面](https://github.com/Plachtaa/seed-vc)<br>"
        "请注意，参考音频若超过 25 秒，则会被自动裁剪至此长度。<br>若源音频和参考音频的总时长超过 30 秒，源音频将被分段处理。"
        "<br>请勾选 'convert style/emotion/accent' 以转换源音频的风格、情感或口音，否则仅执行音色转换。<br>"
        "勾选 'anonymization only' 会无视参考音频而将源音频转换为某种由模型自身决定的 '平均音色'。<br>"

        "Credits to [Vevo](https://github.com/open-mmlab/Amphion/tree/main/models/vc/vevo)"
        )
    inputs = [
        gr.Audio(type="filepath", label="Source Audio / 源音频"),
        gr.Audio(type="filepath", label="Reference Audio / 参考音频"),
        gr.Slider(minimum=1, maximum=200, value=30, step=1, label="Diffusion Steps / 扩散步数",
                  info="30 by default, 50~100 for best quality / 默认为 30，50~100 为最佳质量"),
        gr.Slider(minimum=0.5, maximum=2.0, step=0.1, value=1.0, label="Length Adjust / 长度调整",
                  info="<1.0 for speed-up speech, >1.0 for slow-down speech / <1.0 加速语速，>1.0 减慢语速"),
        gr.Slider(minimum=0.0, maximum=1.0, step=0.1, value=0.0, label="Intelligibility CFG Rate",
                  info="controls pronunciation intelligibility / 控制发音清晰度"),
        gr.Slider(minimum=0.0, maximum=1.0, step=0.1, value=0.7, label="Similarity CFG Rate",
                  info="controls similarity to reference audio / 控制与参考音频的相似度"),
        gr.Slider(minimum=0.1, maximum=1.0, step=0.1, value=0.9, label="Top-p",
                  info="AR model sampling top P"),
        gr.Slider(minimum=0.1, maximum=2.0, step=0.1, value=1.0, label="Temperature",
                  info="AR model sampling temperature"),
        gr.Slider(minimum=1.0, maximum=3.0, step=0.1, value=1.0, label="Repetition Penalty",
                  info="AR model sampling repetition penalty"),
        gr.Checkbox(label="convert style/emotion/accent", value=False),
        gr.Checkbox(label="anonymization only", value=False),
    ]

    examples = [
        ["examples/source/yae_0.wav", "examples/reference/dingzhen_0.wav", 50, 1.0, 0.0, 0.7, 0.9, 1.0, 1.0, False,
         False],
        ["examples/source/jay_0.wav", "examples/reference/azuma_0.wav", 50, 1.0, 0.0, 0.7, 0.9, 1.0, 1.0, False, False],
    ]

    outputs = [
        gr.Audio(label="Stream Output Audio / 流式输出", streaming=True, format='mp3'),
        gr.Audio(label="Full Output Audio / 完整输出", streaming=False, format='wav')
    ]

    return gr.Interface(
        fn=convert_voice_v2_wrapper,
        api_name="convert_voice",
        description=description,
        inputs=inputs,
        outputs=outputs,
        title="Seed Voice Conversion V2 (Voice & Style Conversion)",
        examples=examples,
        cache_examples=False,
    )


