"""Isolate lazy spectral constants, sharing native V1 weights without an infer lock.

V1 initializes its DiT masks with use_kv_cache=False. Spectral constants are
published under short construction locks; no lock spans STFT, inference or yield.
"""
from functools import wraps
import inspect
import threading
from types import FunctionType


def _publish_cuda(torch, device):
    if str(device).startswith("cuda"):
        torch.cuda.current_stream(device).synchronize()


def isolated_mel(callback):
    closure = inspect.getclosurevars(callback)
    native = closure.globals.get("mel_spectrogram")
    arguments = next((v for k, v in closure.nonlocals.items()
                      if k in ("mel_fn_args", "mel_fn_args_f0")), None)
    if not isinstance(native, FunctionType) or not isinstance(arguments, dict):
        raise RuntimeError("V1 mel callback changed; concurrency adapter requires review")
    arguments = dict(arguments)
    namespace = dict(native.__globals__)
    namespace["mel_basis"], namespace["hann_window"] = {}, {}
    function = FunctionType(native.__code__, namespace, native.__name__, native.__defaults__, native.__closure__)
    function.__kwdefaults__ = native.__kwdefaults__
    lock = threading.Lock()

    @wraps(callback)
    def mel(audio):
        torch = namespace["torch"]
        sr, fmax = arguments["sampling_rate"], arguments["fmax"]
        basis_key, window_key = f"{sr}_{fmax}_{audio.device}", f"{sr}_{audio.device}"
        with lock:
            if basis_key not in namespace["mel_basis"]:
                basis = namespace["librosa_mel_fn"](
                    sr=sr, n_fft=arguments["n_fft"], n_mels=arguments["num_mels"],
                    fmin=arguments["fmin"], fmax=fmax,
                )
                basis = torch.from_numpy(basis).float().to(audio.device)
                window = torch.hann_window(arguments["win_size"]).to(audio.device)
                _publish_cuda(torch, audio.device)
                namespace["hann_window"][window_key] = window
                namespace["mel_basis"][basis_key] = basis
        return function(audio, **arguments)
    return mel


def guard_rmvpe_constants(extractor):
    native = extractor.forward
    globals_ = native.__func__.__globals__
    torch, np = globals_["torch"], globals_["np"]
    lock = threading.Lock()

    @wraps(native)
    def forward(audio, keyshift=0, speed=1, center=True):
        key = str(keyshift) + "_" + str(audio.device)
        with lock:
            if key not in extractor.hann_window:
                factor = 2 ** (keyshift / 12)
                window = torch.hann_window(int(np.round(extractor.win_length * factor))).to(audio.device)
                _publish_cuda(torch, audio.device)
                extractor.hann_window[key] = window
            if "privateuseone" in str(audio.device) and not hasattr(extractor, "stft"):
                extractor.stft = globals_["STFT"](
                    filter_length=int(np.round(extractor.n_fft * 2 ** (keyshift / 12))),
                    hop_length=int(np.round(extractor.hop_length * speed)),
                    win_length=int(np.round(extractor.win_length * 2 ** (keyshift / 12))),
                    window="hann",
                ).to(audio.device)
        return native(audio, keyshift=keyshift, speed=speed, center=center)
    extractor.forward = forward


def enable_native_concurrency(model):
    for branch in (model.model, model.model_f0):
        transformer = branch.cfm.estimator.transformer
        if transformer.use_kv_cache or any(
            getattr(module, "kv_cache", None) is not None for module in transformer.modules()
        ):
            raise RuntimeError("V1 mutable KV cache requires request isolation")
    # Independent config-local dictionaries avoid native keys omitting FFT/window
    # geometry. Shared PyTorch parameters and original device/dtype are untouched.
    model.to_mel = isolated_mel(model.to_mel)
    model.to_mel_f0 = isolated_mel(model.to_mel_f0)
    guard_rmvpe_constants(model.rmvpe.mel_extractor)
    return model
