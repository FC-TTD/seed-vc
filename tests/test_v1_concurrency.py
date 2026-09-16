"""Weight-free concurrency: publication, request identity, overlap and close."""
from concurrent.futures import ThreadPoolExecutor
import ast
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest

from hub_runtime.concurrency_v1 import enable_native_concurrency, isolated_mel, guard_rmvpe_constants
from hub_runtime.adapter import final_audio


class Tensor:
    def __init__(self, value):
        self.value = value
        self.device = "cpu"
    def float(self): return self
    def to(self, device):
        self.device = device
        return self


torch = SimpleNamespace(from_numpy=Tensor, hann_window=lambda size: Tensor(size))
np = SimpleNamespace(round=round)
mel_basis, hann_window = {}, {}


def librosa_mel_fn(**kwargs):
    time.sleep(.001)
    return (kwargs['n_fft'], kwargs['n_mels'])


def mel_spectrogram(y, n_fft, num_mels, sampling_rate, hop_size, win_size, fmin, fmax, center=False):
    # Adapter must publish both constants before calling this original function.
    basis = mel_basis[f"{sampling_rate}_{fmax}_{y.device}"]
    window = hann_window[f"{sampling_rate}_{y.device}"]
    assert basis.value == (n_fft, num_mels)
    assert window.value == win_size
    if hasattr(y, 'barrier'): y.barrier.wait(timeout=3)
    return y.value, basis.value, window.value


def callback(fft=1024, win=1024, mels=80, sr=22050):
    mel_fn_args = dict(n_fft=fft, num_mels=mels, sampling_rate=sr, hop_size=256,
                       win_size=win, fmin=0, fmax=None, center=False)
    return lambda x: mel_spectrogram(x, **mel_fn_args)


class Extractor:
    def __init__(self):
        self.hann_window = {}
        self.win_length, self.n_fft, self.hop_length = 1024, 1024, 160
    def forward(self, audio, keyshift=0, speed=1, center=True):
        assert self.hann_window[str(keyshift)+'_'+str(audio.device)].value == int(np.round(self.win_length*2**(keyshift/12)))
        if hasattr(audio,'barrier'): audio.barrier.wait(timeout=3)
        return audio.value, keyshift, speed, center


class NativeFixture:
    def __init__(self):
        transformer = SimpleNamespace(use_kv_cache=False,modules=lambda:[SimpleNamespace(kv_cache=None)])
        self.model = SimpleNamespace(cfm=SimpleNamespace(estimator=SimpleNamespace(transformer=transformer)))
        self.model_f0 = SimpleNamespace(cfm=SimpleNamespace(estimator=SimpleNamespace(transformer=transformer)))
        self.to_mel, self.to_mel_f0 = callback(), callback(2048,2048,128,44100)
        self.rmvpe = SimpleNamespace(mel_extractor=Extractor())
        self.weight = object()
        self.lock = threading.Lock()
        self.active = self.peak = 0
        self.closed=[]
    def convert_voice(self, source, reference, count, f0, barrier):
        with self.lock:
            self.active+=1
            self.peak=max(self.peak,self.active)
        try:
            audio=Tensor((source,reference))
            selected=self.to_mel_f0 if f0 else self.to_mel
            assert selected(audio)[0]==(source,reference)
            if f0: self.rmvpe.mel_extractor.forward(audio)
            barrier.wait(timeout=3)
            for i in range(count):
                time.sleep(.002)
                yield (source,reference,i), (44100 if f0 else 22050, (source,reference,count)) if i==count-1 else None
        finally:
            with self.lock:
                self.active-=1
                self.closed.append((source,reference))


class Concurrency(unittest.TestCase):
    def test_spectral_cache_is_config_local_and_compute_overlaps(self):
        a,b=isolated_mel(callback()),isolated_mel(callback(2048,2048,128))
        barrier=threading.Barrier(4)
        def run(i):
            x=Tensor(i);x.barrier=barrier
            return (a if i%2==0 else b)(x)
        with ThreadPoolExecutor(4) as pool: results=list(pool.map(run,range(4)))
        self.assertEqual([r[0] for r in results],list(range(4)))
        self.assertEqual(results[0][1:],((1024,80),1024))
        self.assertEqual(results[1][1:],((2048,128),2048))
        self.assertEqual(mel_basis,{})
        self.assertEqual(hann_window,{})

    def test_rmvpe_initialization_does_not_serialize_forward(self):
        extractor=Extractor();guard_rmvpe_constants(extractor);barrier=threading.Barrier(4)
        def run(i):
            x=Tensor(i);x.barrier=barrier
            return extractor.forward(x,keyshift=0,speed=1,center=True)
        with ThreadPoolExecutor(4) as pool: results=list(pool.map(run,range(4)))
        self.assertEqual([r[0] for r in results],list(range(4)))
        self.assertEqual(len(extractor.hann_window),1)

    def test_two_and_four_conversions_keep_reference_length_and_weights(self):
        for concurrency in (2,4):
            with self.subTest(concurrency=concurrency):
                model=NativeFixture();weight=model.weight;method=model.convert_voice.__func__
                enable_native_concurrency(model)
                self.assertIs(model.weight,weight)
                self.assertIs(model.convert_voice.__func__,method)
                barrier=threading.Barrier(concurrency)
                def run(i):return final_audio(model.convert_voice('source-'+str(i),'reference-'+str(i),i+2,bool(i%2),barrier))
                with ThreadPoolExecutor(concurrency) as pool: results=list(pool.map(run,range(concurrency)))
                self.assertEqual(model.peak,concurrency)
                for i,r in enumerate(results):self.assertEqual(r,(44100 if i%2 else 22050,('source-'+str(i),'reference-'+str(i),i+2)))
                self.assertEqual(model.active,0)
                self.assertEqual(len(model.closed),concurrency)

    def test_close_one_generator_does_not_close_another(self):
        model=enable_native_concurrency(NativeFixture());barrier=threading.Barrier(2)
        def close_first():
            gen=model.convert_voice('early','ref-a',10,False,barrier)
            next(gen);gen.close()
        def finish_second():return final_audio(model.convert_voice('complete','ref-b',5,True,barrier))
        with ThreadPoolExecutor(2) as pool:
            a=pool.submit(close_first);b=pool.submit(finish_second);a.result();result=b.result()
        self.assertEqual(result,(44100,('complete','ref-b',5)))
        self.assertCountEqual(model.closed,[('early','ref-a'),('complete','ref-b')])
        self.assertEqual(model.active,0)

    def test_exception_in_one_stream_preserves_other_request_and_cleanup(self):
        model=enable_native_concurrency(NativeFixture());barrier=threading.Barrier(2)
        def fail():
            gen=model.convert_voice('error','ref-error',8,False,barrier)
            try:
                next(gen)
                raise ValueError('request-local postprocessing failure')
            finally:gen.close()
        with ThreadPoolExecutor(2) as pool:
            a=pool.submit(fail)
            b=pool.submit(lambda:final_audio(model.convert_voice('good','ref-good',4,True,barrier)))
            with self.assertRaisesRegex(ValueError,'request-local'):a.result()
            self.assertEqual(b.result(),(44100,('good','ref-good',4)))
        self.assertEqual(model.active,0)
        self.assertCountEqual(model.closed,[('error','ref-error'),('good','ref-good')])

    def test_future_upstream_mutable_kv_fails_before_unlocking(self):
        model=NativeFixture();model.model.cfm.estimator.transformer.use_kv_cache=True
        with self.assertRaisesRegex(RuntimeError,'mutable KV'):enable_native_concurrency(model)

    def test_native_no_kv_contract_and_unlimited_ui_callback(self):
        root=Path(__file__).resolve().parents[1]
        tree=ast.parse((root/'modules/diffusion_transformer.py').read_text())
        dit=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='DiT')
        setup=next(n for n in dit.body if isinstance(n,ast.FunctionDef) and n.name=='setup_caches')
        call=next(n for n in ast.walk(setup) if isinstance(n,ast.Call))
        self.assertTrue(any(k.arg=='use_kv_cache' and isinstance(k.value,ast.Constant) and k.value.value is False for k in call.keywords))
        ui=ast.parse((root/'hub_runtime/ui.py').read_text())
        interface=next(n for n in ast.walk(ui) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='Interface')
        self.assertTrue(any(k.arg=='concurrency_limit' and isinstance(k.value,ast.Constant) and k.value.value is None for k in interface.keywords))


if __name__=='__main__':unittest.main()
