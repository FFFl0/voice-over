"""Роли по голосам: реплики группируются по звучанию голоса, группы становятся персонажами.

Для каждой реплики считается «отпечаток голоса» (эмбеддинг) моделью WeSpeaker ResNet34
(ONNX, ~25 МБ, скачивается один раз с Hugging Face). Если модели нет — запасной простой
отпечаток по тембру (MFCC): он различает мужской/женский/детский голос, но хуже.
Затем реплики объединяются в группы (агломеративная кластеризация по косинусному
расстоянию). Если часть реплик уже размечена, группа получает роль, которая в ней
встречается чаще всего, — достаточно отметить по 2–3 реплики каждого персонажа."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import soundfile as sf

from .ffmpeg import Cancelled
from .models import Project

SR = 16000
MODEL_REPO = "Wespeaker/wespeaker-voxceleb-resnet34-LM"
MIN_DUR = 0.6          # короче — голос не определить надёжно
MAX_DUR = 10.0
AUTO_THRESHOLD = 0.55  # косинусное расстояние, при котором группы ещё объединяются (модель)
AUTO_THRESHOLD_MFCC = 0.25

ProgressCb = Callable[[float, str], None]


# ---------------------------------------------------------------- признаки
def _mel_bank(n_fft: int, n_mels: int, sr: int, fmin: float = 20.0, fmax: float = 0.0):
    fmax = fmax or sr / 2
    mel = lambda f: 1127.0 * np.log1p(np.asarray(f) / 700.0)    # noqa: E731
    pts = np.linspace(mel(fmin), mel(fmax), n_mels + 2)
    freqs = mel(np.fft.rfftfreq(n_fft, 1 / sr))
    bank = np.zeros((n_mels, len(freqs)), np.float32)
    for m in range(n_mels):
        lo, c, hi = pts[m], pts[m + 1], pts[m + 2]
        up = (freqs - lo) / (c - lo)
        down = (hi - freqs) / (hi - c)
        bank[m] = np.maximum(0, np.minimum(up, down))
    return bank


_BANKS: dict = {}


def fbank(x: np.ndarray, sr: int = SR, n_mels: int = 80) -> np.ndarray:
    """Лог-мел признаки как в Kaldi (25 мс / 10 мс, окно Пови), с вычитанием среднего."""
    flen, hop, n_fft = int(0.025 * sr), int(0.010 * sr), 512
    if len(x) < flen:
        x = np.pad(x, (0, flen - len(x)))
    n = 1 + (len(x) - flen) // hop
    idx = np.arange(flen)[None, :] + hop * np.arange(n)[:, None]
    fr = x[idx].astype(np.float32) * 32768.0
    fr -= fr.mean(axis=1, keepdims=True)
    fr = np.concatenate([fr[:, :1] * (1 - 0.97), fr[:, 1:] - 0.97 * fr[:, :-1]], axis=1)
    win = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(flen) / (flen - 1))) ** 0.85
    spec = np.abs(np.fft.rfft(fr * win, n_fft)) ** 2
    key = (n_fft, n_mels, sr)
    if key not in _BANKS:
        _BANKS[key] = _mel_bank(n_fft, n_mels, sr)
    feats = np.log(np.maximum(spec @ _BANKS[key].T, 1.1920929e-07))
    return (feats - feats.mean(axis=0)).astype(np.float32)


def mfcc_embedding(x: np.ndarray) -> np.ndarray:
    """Запасной отпечаток голоса: статистики MFCC по громким кадрам."""
    f = fbank(x, SR, 40)
    e = f.sum(axis=1)
    f = f[e >= np.percentile(e, 40)] if len(f) > 10 else f
    n = f.shape[1]
    k = np.arange(n)
    dct = np.cos(np.pi / n * (k[:, None] + 0.5) * np.arange(1, 21)[None, :])
    c = f @ dct
    return np.concatenate([c.mean(axis=0), c.std(axis=0)]).astype(np.float32)


class OnnxEmbedder:
    def __init__(self, model_path: str):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        self.sess = ort.InferenceSession(model_path, opts, providers=["CPUExecutionProvider"])
        self.inp = self.sess.get_inputs()[0].name

    def __call__(self, x: np.ndarray) -> np.ndarray:
        feats = fbank(x)[None]
        return np.asarray(self.sess.run(None, {self.inp: feats})[0][0], np.float32)


def load_model(cache_dir: str) -> Optional[str]:
    """Путь к ONNX-модели (скачать при первом запуске) или None, если нет сети/модулей."""
    d = Path(cache_dir)
    found = sorted(d.rglob("*.onnx")) if d.exists() else []
    if found:
        return str(found[0])
    try:
        import onnxruntime  # noqa: F401
        from huggingface_hub import HfApi, hf_hub_download
        files = [f for f in HfApi().list_repo_files(MODEL_REPO) if f.endswith(".onnx")]
        if not files:
            return None
        return hf_hub_download(MODEL_REPO, files[0], local_dir=str(d))
    except Exception:  # noqa: BLE001  нет интернета или модулей — запасной способ
        return None


# ---------------------------------------------------------------- кластеризация
def cluster(emb: np.ndarray, n_clusters: Optional[int] = None,
            threshold: float = AUTO_THRESHOLD) -> np.ndarray:
    """Агломеративная кластеризация (средняя связь, косинусное расстояние).
    n_clusters=None — по порогу расстояния."""
    n = len(emb)
    if n == 0:
        return np.zeros(0, int)
    x = emb / np.maximum(np.linalg.norm(emb, axis=1, keepdims=True), 1e-9)
    dist = 1.0 - x @ x.T
    np.fill_diagonal(dist, np.inf)
    sizes = np.ones(n)
    labels = np.arange(n)
    active = np.ones(n, bool)
    target = max(1, n_clusters or 1)
    while active.sum() > target:
        i, j = divmod(int(np.argmin(dist)), n)
        if n_clusters is None and dist[i, j] > threshold:
            break
        # средняя связь: новое расстояние — взвешенное среднее
        new = (dist[i] * sizes[i] + dist[j] * sizes[j]) / (sizes[i] + sizes[j])
        dist[i, :] = new
        dist[:, i] = new
        dist[i, i] = np.inf
        dist[j, :] = np.inf
        dist[:, j] = np.inf
        sizes[i] += sizes[j]
        active[j] = False
        labels[labels == j] = i
    _, out = np.unique(labels, return_inverse=True)
    return out


# ---------------------------------------------------------------- проект
@dataclass
class DiarizeResult:
    assigned: int = 0
    new_characters: list = field(default_factory=list)
    skipped_short: int = 0
    used_model: bool = False


def _audio_source(project: Project) -> Optional[Path]:
    vo = project.project_dir / "vocals.wav"
    if vo.exists():
        return vo
    p = project.resolve(project.original_wav)
    return p if p and p.exists() else None


def _segment(f: sf.SoundFile, a: float, b: float) -> np.ndarray:
    sr = f.samplerate
    i0, i1 = max(0, int(a * sr)), min(f.frames, int(b * sr))
    if i1 <= i0:
        return np.zeros(0, np.float32)
    f.seek(i0)
    x = f.read(i1 - i0, dtype="float32", always_2d=True).mean(axis=1)
    if sr != SR:
        m = int(len(x) * SR / sr)
        x = np.interp(np.linspace(0, len(x) - 1, m), np.arange(len(x)), x).astype(np.float32)
    return x


def assign_roles(project: Project, only_unassigned: bool = True,
                 n_voices: Optional[int] = None, model_dir: str = "",
                 progress: Optional[ProgressCb] = None,
                 cancel: Optional[Callable[[], bool]] = None,
                 embedder: Optional[Callable[[np.ndarray], np.ndarray]] = None) -> DiarizeResult:
    src = _audio_source(project)
    if src is None:
        raise RuntimeError("Нет звука оригинала: откройте видео заново или извлеките звук.")
    res = DiarizeResult()
    if embedder is None:
        if progress:
            progress(0.0, "Загрузка модели голосов (при первом запуске скачивается ~25 МБ)…")
        path = load_model(model_dir) if model_dir else None
        if path:
            embedder = OnnxEmbedder(path)
            res.used_model = True
        else:
            embedder = mfcc_embedding
    else:
        res.used_model = True
    thr = AUTO_THRESHOLD if res.used_model else AUTO_THRESHOLD_MFCC
    lines = [ln for ln in project.lines if ln.enabled and ln.duration >= MIN_DUR]
    res.skipped_short = sum(1 for ln in project.lines if ln.enabled and ln.duration < MIN_DUR)
    embs, keep = [], []
    with sf.SoundFile(str(src)) as f:
        for k, ln in enumerate(lines):
            if cancel and cancel():
                raise Cancelled()
            x = _segment(f, ln.start, min(ln.end, ln.start + MAX_DUR))
            if len(x) < MIN_DUR * SR * 0.8 or float(np.abs(x).max(initial=0)) < 1e-4:
                continue
            embs.append(embedder(x))
            keep.append(ln)
            if progress and k % 5 == 0:
                progress(0.05 + 0.9 * k / max(1, len(lines)),
                         f"Отпечатки голосов: {k + 1} из {len(lines)}")
    if not keep:
        return res
    labels = cluster(np.stack(embs), n_voices, thr)
    # группа → персонаж: по уже размеченным репликам этой группы
    mapping: dict[int, str] = {}
    for g in np.unique(labels):
        votes = Counter(ln.character_id for ln, lab in zip(keep, labels)
                        if lab == g and project.character(ln.character_id))
        if votes:
            cid, n = votes.most_common(1)[0]
            if n / sum(votes.values()) >= 0.6:
                mapping[int(g)] = cid
    k = 1
    for g in np.unique(labels):
        if int(g) in mapping:
            continue
        members = [ln for ln, lab in zip(keep, labels) if lab == g]
        if only_unassigned and all(project.character(ln.character_id) for ln in members):
            continue
        while project.character_by_name(f"Голос {k}"):
            k += 1
        ch = project.add_character(f"Голос {k}")
        res.new_characters.append(ch)
        mapping[int(g)] = ch.id
    for ln, lab in zip(keep, labels):
        has = project.character(ln.character_id) is not None
        if only_unassigned and has:
            continue
        cid = mapping.get(int(lab))
        if cid and cid != ln.character_id:
            ln.character_id = cid
            res.assigned += 1
    if progress:
        progress(1.0, "Роли назначены")
    return res
