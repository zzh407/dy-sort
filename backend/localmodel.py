"""内置本地视觉模型：下载 CLIP ONNX（图像 + 文本编码器）并在进程内推理。

- 模型文件按需下载到 `local_model_dir`（默认 /app/.cache/models，随 cache 卷持久化）；
- 用 hf-mirror.com（国内可直连）下载，可用 `local_model_mirror` 覆盖；
- 文本侧只算一次：把标签的中文提示词编码成向量并缓存成 .npy，之后每张图只跑视觉编码器；
- 全程 CPU、无外部服务依赖（不需要 Ollama 之类）。

模型：Xenova/clip-vit-base-patch32（MIT），quantized 约 155MB，fp32 约 600MB。
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import urllib.request
from pathlib import Path

try:  # 允许在没装 numpy 的环境（比如本地只跑单测）导入本模块
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

from . import settings

log = logging.getLogger("dy-sort.localmodel")

REPO = "Xenova/clip-vit-base-patch32"
VARIANTS = {
    "quantized": {
        "vision": ("onnx/vision_model_quantized.onnx", 89_117_001),
        "text": ("onnx/text_model_quantized.onnx", 64_504_507),
        "tokenizer": ("tokenizer.json", 2_224_119),
    },
    "fp32": {
        "vision": ("onnx/vision_model.onnx", 351_685_709),
        "text": ("onnx/text_model.onnx", 254_058_553),
        "tokenizer": ("tokenizer.json", 2_224_119),
    },
    # ViT-L/14：更大更准，代价是 434MB 与更慢的 CPU 推理
    "large": {
        "vision": ("onnx/vision_model_quantized.onnx", 307_001_500),
        "text": ("onnx/text_model_quantized.onnx", 124_987_663),
        "tokenizer": ("tokenizer.json", 2_224_081),
    },
}

# 不同规格对应的 HF 仓库（默认 ViT-B/32）
VARIANT_REPO = {"large": "Xenova/clip-vit-large-patch14"}

DEFAULT_MIRROR = "https://hf-mirror.com"

# 中文标签 -> 英文提示词（CLIP 的文本塔是英文训练的）
STYLE_PROMPTS = {
    "丝袜": ["black sheer stockings on legs", "pantyhose with a skirt", "sheer black tights"],
    "网袜": ["black fishnet stockings", "fishnet tights on legs"],
    "过膝袜": ["white thigh high socks", "black knee high socks"],
    "瑜伽裤": ["yoga pants in a gym", "grey leggings workout"],
    "打底裤": ["tight leggings as pants", "black skinny leggings"],
    "牛仔裤": ["blue denim jeans", "denim jeans with back pockets"],
    "短裤": ["a woman wearing short denim shorts", "short tight shorts with bare legs", "hot pants"],
    "阔腿裤": ["wide leg trousers", "loose palazzo pants"],
    "工装裤": ["cargo pants", "utility pants"],
    "运动裤": ["sweatpants", "track pants"],
    "背带裤": ["overalls", "suspender pants"],
    "连体裤": ["a jumpsuit", "a one piece romper"],
    "裙装": ["a skirt outfit", "a woman in a skirt"],
    "连衣裙": ["a one piece dress", "a long dress covering the body", "a dress and heels"],
    "半身裙": ["a mini skirt with a top", "an a-line skirt", "a skirt and a blouse"],
    "百褶裙": ["a pleated mini skirt", "a school uniform pleated skirt"],
    "包臀裙": ["a tight black mini skirt", "a bodycon mini skirt", "a short pencil skirt"],
    "吊带裙": ["a slip dress with thin straps", "a satin camisole dress"],
    "旗袍": ["a chinese qipao dress", "a cheongsam"],
    "汉服": ["hanfu traditional chinese costume", "ancient chinese dress"],
    "洛丽塔": ["a lolita dress with frills", "a frilly lolita outfit"],
    "JK制服": ["a japanese school uniform with pleated skirt", "a sailor collar uniform"],
    "睡裙": ["a nightgown", "a sleep dress"],
    "礼服": ["an evening gown", "a formal dress"],
    "婚纱": ["a wedding dress", "a bride in white"],
    "T恤": ["a plain white t-shirt", "a polo shirt"],
    "衬衫": ["a button up shirt", "a blouse"],
    "卫衣": ["a hoodie", "a sweatshirt"],
    "针织衫": ["a knit cardigan over a top", "a knitted sweater"],
    "毛衣": ["a sweater", "a wool pullover"],
    "西装": ["a blazer", "a suit jacket"],
    "套装": ["a matching two piece suit", "a formal suit set"],
    "马甲": ["a vest", "a waistcoat"],
    "背心": ["a tank top", "a sleeveless top"],
    "吊带": ["a camisole top", "a spaghetti strap top"],
    "露肩装": ["an off shoulder top", "a strapless top"],
    "露脐装": ["a crop top showing the midriff", "a belly top"],
    "大衣": ["a long wool coat", "a winter overcoat"],
    "风衣": ["a trench coat", "a windbreaker"],
    "羽绒服": ["a puffer jacket", "a down jacket"],
    "皮衣": ["a leather jacket", "a black leather biker jacket"],
    "高跟鞋": ["a woman wearing high heels", "black high heel shoes",
             "stiletto high heels"],
    "泳装": ["a bikini", "a swimsuit at the beach"],
    "运动装": ["sportswear", "a sports bra and shorts"],
    "制服": ["a maid costume with apron", "a nurse uniform costume"],
    "睡衣": ["a pajama set in bed", "sleepwear at home"],
    "内衣": ["lingerie", "a bra and underwear"],
    "家居服": ["loungewear", "a home outfit"],
    "其他": ["casual everyday clothes", "a plain outfit"],
}

BODY_PROMPTS = {
    "大胸": ["a woman with a large bust", "a busty woman"],
    "小胸": ["a woman with a small flat chest", "a slim bust"],
    "长腿": ["a woman with long legs", "very long legs"],
    "高个": ["a tall woman", "a tall slender figure"],
    "娇小": ["a petite short woman", "a small body frame"],
    "纤细": ["a very slim thin woman", "a slender figure"],
    "微胖": ["a slightly plump woman", "a soft curvy body"],
    "丰满": ["a curvy voluptuous woman", "a full figure"],
    "有沟": ["visible cleavage", "a low cut top showing cleavage",
             "a woman showing cleavage"],
    "健美": ["an athletic toned body", "a fit sporty figure"],
    "细腰": ["a very thin waist", "a tiny waist"],
    "翘臀": ["a prominent round butt", "curvy hips from behind"],
    "直角肩": ["straight square shoulders", "broad flat shoulders"],
    "锁骨": ["visible collarbones", "a prominent collarbone"],
    "马甲线": ["visible ab lines", "a toned stomach"],
    "腹肌": ["six pack abs", "a muscular abdomen"],
    "肌肉": ["muscular arms and shoulders", "a muscular body"],
    "长颈": ["a long slender neck", "an elegant swan neck"],
    "沙漏型": ["an hourglass figure", "wide hips and a narrow waist"],
    "梨形": ["a pear shaped body", "hips wider than shoulders"],
    "苹果型": ["an apple shaped body", "a round midsection"],
}

_MEAN = (np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
         if np is not None else None)
_STD = (np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
        if np is not None else None)

# 粗粒度（大类）映射：CLIP 分不清同类细标签，但「裙/裤/袜」这种大差异很稳
COARSE_STYLE = {
    "袜类": ["丝袜", "网袜", "过膝袜"],
    "裤装": ["瑜伽裤", "打底裤", "牛仔裤", "短裤", "阔腿裤", "工装裤",
             "运动裤", "背带裤", "连体裤"],
    "裙装": ["裙装", "连衣裙", "半身裙", "百褶裙", "包臀裙", "吊带裙",
             "旗袍", "汉服", "洛丽塔", "JK制服", "礼服", "婚纱"],
    "上装": ["T恤", "衬衫", "卫衣", "针织衫", "毛衣", "西装", "套装", "马甲",
             "背心", "吊带", "露肩装", "露脐装"],
    "外套": ["大衣", "风衣", "羽绒服", "皮衣"],
    "鞋类": ["高跟鞋"],
    "泳装运动": ["泳装", "运动装"],
    "制服角色": ["制服"],
    "睡衣家居": ["睡衣", "睡裙", "家居服", "内衣"],
    "其他": ["其他"],
}
COARSE_BODY = {
    "胸部": ["大胸", "小胸", "丰满", "有沟"],
    "腿部": ["长腿"],
    "身高": ["高个", "娇小"],
    "腰腹": ["细腰", "马甲线", "腹肌"],
    "肩颈": ["直角肩", "锁骨", "长颈"],
    "体态": ["纤细", "微胖", "健美", "肌肉"],
    "身形": ["沙漏型", "梨形", "苹果型", "翘臀"],
}
COARSE_LABELS = set(COARSE_STYLE) | set(COARSE_BODY)

# 身材单图只报「最像的一个大类」。36 张人工标注（12 博主 x 3）上实测：
# 多标签分支的第二名基本是噪声，且「分数接近时只追加第二名」会把真正的 top1 丢掉；
# 改成 top1-only 后精确率 0.565→0.783、召回率 0.228→0.316（阈值 0.25），
# 博主级聚合（10 张以上）仍然会得到多标签。阈值 0.25 见 docs/body-eval.md。
BODY_MIN_SCORE = 0.25


def _aggregate(scores: dict[str, float], groups: dict[str, list[str]]) -> dict[str, float]:
    """把细标签分数聚到大类：同类取最高分。"""
    out: dict[str, float] = {}
    for group, children in groups.items():
        vals = [scores[c] for c in children if c in scores]
        if vals:
            out[group] = max(vals)
    return out

_lock = threading.Lock()
_models: dict[str, "LocalModel"] = {}


class LocalModelError(Exception):
    pass


# ---------- 下载 ----------
def _mirror() -> str:
    return (settings.get("local_model_mirror") or DEFAULT_MIRROR).rstrip("/")


def model_dir() -> Path:
    return Path(settings.get("local_model_dir") or "/app/.cache/models")


def variant() -> str:
    v = (settings.get("local_model_variant") or "quantized").strip().lower()
    return v if v in VARIANTS else "quantized"


def model_name(var: str | None = None) -> str:
    """给界面看的模型名。"""
    var = var or variant()
    return {"quantized": "clip-vit-b32-quantized",
            "fp32": "clip-vit-b32-fp32",
            "large": "clip-vit-l14-quantized"}.get(var, f"clip-{var}")


def model_paths(var: str | None = None) -> dict[str, Path]:
    var = var or variant()
    d = model_dir() / var
    return {key: d / Path(rel).name for key, (rel, _size) in VARIANTS[var].items()}


def model_ready(var: str | None = None) -> bool:
    return all(p.exists() and p.stat().st_size > 1_000_000
               for p in model_paths(var).values())


def _download(url: str, path: Path, expect: int, progress) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    done = 0
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 dy-sort"})
    with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as f:
        total = int(resp.headers.get("Content-Length") or expect or 0)
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress("download", done, total)
    if expect:
        size = tmp.stat().st_size
        if size < expect * 0.95:
            tmp.unlink(missing_ok=True)
            raise LocalModelError(f"下载不完整: {path.name} {size}/{expect}")
    tmp.replace(path)
    log.info("下载完成 %s（%.1f MB，%.0fs）", path.name, path.stat().st_size / 1e6,
             time.time() - t0)


def ensure_model(progress=None, var: str | None = None) -> Path:
    """确保模型文件存在；缺哪个下哪个。返回模型目录。"""
    var = var or variant()
    paths = model_paths(var)
    d = paths["vision"].parent
    d.mkdir(parents=True, exist_ok=True)
    if not model_ready(var) and not settings.get_bool("local_model_auto_download", True):
        raise LocalModelError(
            "本地模型还没下载，且已关闭自动下载：请在设置里打开「首次识别时自动下载」，"
            "或把模型文件放进 " + str(d))
    for key, (rel, size) in VARIANTS[var].items():
        target = paths[key]
        if target.exists() and target.stat().st_size > 1_000_000:
            continue
        url = f"{_mirror()}/{VARIANT_REPO.get(var, REPO)}/resolve/main/{rel}"
        last = None
        for attempt in range(3):
            try:
                _download(url, target, size, progress)
                last = None
                break
            except Exception as e:  # noqa: BLE001
                last = e
                log.warning("下载失败（第 %d 次）%s: %s", attempt + 1, url, e)
                time.sleep(2 * (attempt + 1))
        if last is not None:
            raise LocalModelError(f"模型下载失败：{rel}（{last}）")
    return d


# ---------- 推理 ----------
class LocalModel:
    def __init__(self, var: str | None = None, progress=None):
        if np is None:
            raise LocalModelError("缺少 numpy，无法使用本地模型（重新构建镜像即可）")
        self.var = var or variant()
        d = ensure_model(progress, self.var)
        paths = model_paths(self.var)
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as e:  # noqa: BLE001
            raise LocalModelError(
                f"缺少本地推理依赖（onnxruntime / tokenizers）：{e}") from e
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, min(8, (os.cpu_count() or 4) // 2))
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        me = self

        class _Tok:
            def __init__(self):
                self.t = Tokenizer.from_file(str(paths["tokenizer"]))
                self.t.enable_truncation(max_length=77)
                self.t.enable_padding(length=77)

        self._tok = _Tok()
        self._vis = ort.InferenceSession(str(paths["vision"]),
                                         sess_options=opts,
                                         providers=["CPUExecutionProvider"])
        self._txt = ort.InferenceSession(str(paths["text"]),
                                         sess_options=opts,
                                         providers=["CPUExecutionProvider"])
        self._emb_cache: dict[tuple, tuple[np.ndarray, list[str]]] = {}
        self._emb_files: dict[tuple, Path] = {}
        log.info("本地模型就绪: %s（%s）", self.var, d)

    # 文本侧：标签 -> 向量（按标签集合缓存到磁盘）
    def _label_embeddings(self, labels: list[str], kind: str) -> tuple[np.ndarray, list[str]]:
        key = (kind, tuple(labels))
        if key in self._emb_cache:
            return self._emb_cache[key]
        prompts: list[str] = []
        index: list[str] = []
        table = STYLE_PROMPTS if kind == "style" else BODY_PROMPTS
        for lab in labels:
            for p in table.get(lab, [f"a photo of {lab}"]):
                prompts.append(p)
                index.append(lab)
        cache_dir = model_dir() / self.var
        digest = hashlib.sha1(("|".join(prompts)).encode("utf-8")).hexdigest()[:16]
        cache = cache_dir / f"text_emb_{kind}_{digest}.npz"
        if cache.exists():
            data = np.load(cache)
            emb = data["emb"]
        else:
            enc = self._tok.t.encode_batch(prompts)
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            emb = self._txt.run(None, {"input_ids": ids})[0].astype(np.float32)
            norm = np.linalg.norm(emb, axis=-1, keepdims=True)
            emb = emb / np.maximum(norm, 1e-8)
            np.savez_compressed(cache, emb=emb)
        self._emb_cache[key] = (emb, index)
        return emb, index

    def _image_embedding(self, raw: bytes) -> np.ndarray:
        from io import BytesIO

        from PIL import Image
        with Image.open(BytesIO(raw)) as im:
            im = im.convert("RGB")
            w, h = im.size
            scale = 224 / max(1, min(w, h))
            im = im.resize((max(224, int(round(w * scale))),
                            max(224, int(round(h * scale)))), Image.BICUBIC)
            w, h = im.size
            left, top = (w - 224) // 2, (h - 224) // 2
            im = im.crop((left, top, left + 224, top + 224))
            arr = np.asarray(im, dtype=np.float32) / 255.0
        arr = (arr - _MEAN) / _STD
        tensor = arr.transpose(2, 0, 1)[None, ...]
        out = self._vis.run(None, {"pixel_values": tensor})[0][0].astype(np.float32)
        return out / max(1e-8, float(np.linalg.norm(out)))

    def classify(self, raw: bytes, labs: list[str],
                 bodies: list[str]) -> dict:
        """单图 -> {style, body[], confidence, note}"""
        emb, index = self._label_embeddings(labs, "style")
        img = self._image_embedding(raw)
        sims = emb @ img
        best = {}
        for lab, s in zip(index, sims):
            if lab not in best or s > best[lab]:
                best[lab] = float(s)
        coarse = (settings.get("local_model_granularity") or "coarse").lower() == "coarse"
        if coarse:
            best = _aggregate(best, COARSE_STYLE)
        ranked = sorted(best.items(), key=lambda kv: -kv[1])
        if not ranked:
            return {"style": "", "body": [], "confidence": 0.0, "note": "无标签"}
        style, top = ranked[0]
        # 用 softmax 把相似度转成近似置信度
        vals = np.array([v for _, v in ranked], dtype=np.float32)
        probs = np.exp((vals - vals.max()) * 50.0)
        probs /= probs.sum()
        conf = float(probs[0])

        body_out: list[str] = []
        if bodies:
            bemb, bindex = self._label_embeddings(bodies, "body")
            bsims = bemb @ img
            bbest: dict[str, float] = {}
            for lab, s in zip(bindex, bsims):
                bbest[lab] = max(bbest.get(lab, -1.0), float(s))
            if coarse:
                bbest = _aggregate(bbest, COARSE_BODY)
            ordered = sorted(bbest.items(), key=lambda kv: -kv[1])
            # 只报 top1：阈值以下视为没有可判读的身材特征，宁缺勿滥。
            if ordered and ordered[0][1] >= BODY_MIN_SCORE:
                body_out.append(ordered[0][0])
        return {"style": style, "body": body_out, "confidence": round(conf, 3),
                "note": f"本地模型 {self.var}"}


def get_model(progress=None) -> LocalModel:
    """按 variant 缓存单例（进程内只加载一次）。"""
    var = variant()
    with _lock:
        if var not in _models:
            _models[var] = LocalModel(var, progress)
        return _models[var]


def reset_models() -> None:
    with _lock:
        _models.clear()
