"""运行时配置：config/config.yaml 持久化，便于目录映射与手工编辑。

- 首次运行自动生成带注释的模板
- 网页「设置」保存后写回 config.yaml（保留注释与格式）
- yaml 中留空/缺失的键回落到环境变量默认值
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from ruamel.yaml import YAML

CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/app/config"))
CONFIG_FILE = CONFIG_DIR / "config.yaml"

# 环境变量默认值（yaml 中对应键留空时生效）
_ENV_DEFAULTS = {
    "emby_url": os.environ.get("EMBY_URL", ""),
    "emby_api_key": os.environ.get("EMBY_API_KEY", ""),
    "emby_library_prefix": os.environ.get("EMBY_LIBRARY_PREFIX", "DY_"),
    "media_subdir": os.environ.get("MEDIA_SUBDIR", ""),
    "host_video_root": os.environ.get("HOST_VIDEO_ROOT", ""),
    "page_size": os.environ.get("PAGE_SIZE", "60"),
    "scan_interval": os.environ.get("SCAN_INTERVAL", "0"),
    "enable_delete": os.environ.get("ENABLE_DELETE", "false"),
    "emby_auto_library": os.environ.get("EMBY_AUTO_LIBRARY", "true"),
    "emby_library_max_videos": os.environ.get("EMBY_LIBRARY_MAX_VIDEOS", "2000"),
    "style_labels": os.environ.get("STYLE_LABELS", (
        "丝袜,网袜,过膝袜,瑜伽裤,打底裤,牛仔裤,短裤,阔腿裤,工装裤,运动裤,背带裤,连体裤,"
        "裙装,连衣裙,半身裙,百褶裙,包臀裙,吊带裙,旗袍,汉服,洛丽塔,JK制服,睡裙,礼服,婚纱,"
        "T恤,衬衫,卫衣,针织衫,毛衣,西装,套装,马甲,背心,吊带,露肩装,露脐装,"
        "大衣,风衣,羽绒服,皮衣,高跟鞋,泳装,运动装,制服,睡衣,内衣,家居服,其他")),
    "body_labels": os.environ.get("BODY_LABELS", (
        "大胸,小胸,长腿,高个,娇小,纤细,微胖,丰满,有沟,健美,"
        "细腰,翘臀,直角肩,锁骨,马甲线,腹肌,肌肉,长颈,沙漏型,梨形,苹果型")),
    "vision_base_url": os.environ.get("VISION_BASE_URL",
                                      "https://open.bigmodel.cn/api/paas/v4"),
    "vision_api_key": os.environ.get("VISION_API_KEY", ""),
    "vision_model": os.environ.get("VISION_MODEL", "glm-4v-flash"),
    "vision_max_images": os.environ.get("VISION_MAX_IMAGES", "3"),
    "vision_sample_videos": os.environ.get("VISION_SAMPLE_VIDEOS", "10"),
    "vision_batch_images": os.environ.get("VISION_BATCH_IMAGES", "1"),
    "vision_concurrency": os.environ.get("VISION_CONCURRENCY", "2"),
    # 识别引擎：local（默认，内置 CLIP）| llm（云端大模型）| rules（关键词规则）
    "recognition_engine": os.environ.get("RECOGNITION_ENGINE", "local"),
    # 内置本地模型：自动下载 CLIP ONNX 到 local_model_dir 并在进程内推理
    "local_model_variant": os.environ.get("LOCAL_MODEL_VARIANT", "quantized"),
    "local_model_dir": os.environ.get("LOCAL_MODEL_DIR", "/app/.cache/models"),
    "local_model_mirror": os.environ.get("LOCAL_MODEL_MIRROR", "https://hf-mirror.com"),
    "local_model_auto_download": os.environ.get("LOCAL_MODEL_AUTO_DOWNLOAD", "true"),
    # 输出粒度：coarse（大类，默认）| fine（细标签）
    "local_model_granularity": os.environ.get("LOCAL_MODEL_GRANULARITY", "coarse"),
    # 标签关键词：默认空；格式「标签:关键词1,关键词2; 标签2:关键词」（用于本地匹配与模型输出归位）
    "label_keywords": os.environ.get("LABEL_KEYWORDS", ""),
    "body_keywords": os.environ.get("BODY_KEYWORDS", ""),
}

# 旧版本硬编码的同义词表：仅用于升级时一次性写入 config.yaml（之后以配置为准）
LEGACY_LABEL_KEYWORDS = {
    "丝袜": ["黑丝", "连裤袜", "厚丝", "薄丝", "丝"],
    "网袜": ["渔网"],
    "过膝袜": ["长筒袜", "大腿袜"],
    "瑜伽裤": ["瑜伽"],
    "打底裤": ["紧身裤", "安全裤"],
    "牛仔裤": ["牛仔"],
    "短裤": ["热裤"],
    "阔腿裤": ["拖地裤"],
    "工装裤": ["工装"],
    "运动裤": ["卫裤", "束脚裤"],
    "背带裤": ["吊带裤"],
    "连体裤": ["连身裤"],
    "连衣裙": ["连身裙"],
    "半身裙": ["分体裙"],
    "百褶裙": ["褶裙"],
    "包臀裙": ["铅笔裙"],
    "吊带裙": ["吊带连衣裙"],
    "裙装": ["裙子", "裙"],
    "T恤": ["t恤", "短袖", "t恤衫"],
    "衬衫": ["衬衣"],
    "卫衣": ["帽衫"],
    "针织衫": ["针织", "开衫"],
    "毛衣": ["毛线衣"],
    "西装": ["西服", "小西装"],
    "套装": ["成套"],
    "马甲": ["背心外套"],
    "背心": ["无袖"],
    "吊带": ["吊带衫", "吊带背心"],
    "露肩装": ["露肩", "一字肩", "抹胸"],
    "露脐装": ["露脐", "短上衣", "肚兜"],
    "大衣": ["呢子"],
    "羽绒服": ["羽绒"],
    "皮衣": ["皮夹克"],
    "高跟鞋": ["高跟", "细跟", "高跟凉鞋", "尖头高跟"],
    "泳装": ["泳", "比基尼"],
    "运动装": ["运动", "健身", "球衣"],
    "制服": ["护士服", "空姐服", "学生装"],
    "JK制服": ["jk"],
    "汉服": ["古装"],
    "洛丽塔": ["lolita", "lo裙"],
    "睡裙": ["睡袍"],
    "睡衣": ["成套睡衣"],
    "家居服": ["居家服"],
    "内衣": ["文胸", "胸衣"],
    "礼服": ["晚礼服"],
}

LEGACY_BODY_KEYWORDS = {
    "大胸": ["巨乳", "胸大", "上围丰满"],
    "小胸": ["平胸", "飞机场"],
    "长腿": ["大长腿", "腿长"],
    "高个": ["高挑", "个子高"],
    "娇小": ["小个子", "矮"],
    "纤细": ["瘦", "苗条", "骨感"],
    "微胖": ["肉肉", "多肉"],
    "丰满": ["曲线美"],
    "有沟": ["乳沟", "事业线", "露沟"],
    "健美": ["运动型身材"],
    "细腰": ["小蛮腰", "腰细"],
    "翘臀": ["蜜桃臀", "臀部"],
    "直角肩": ["平肩"],
    "马甲线": ["腹部线条"],
    "腹肌": ["六块腹肌"],
    "肌肉": ["健身"],
    "长颈": ["天鹅颈"],
    "沙漏型": ["沙漏"],
    "梨形": ["梨形身材"],
    "苹果型": ["苹果型身材"],
}

# 旧版默认标签集（48 风格 / 20 身材）：升级迁移时，配置里恰好等于旧默认值的，
# 自动替换成新默认值（49 / 21，含高跟鞋 / 有沟）。用户自定义过的列表不在此列。
LEGACY_STYLE_LABELS = (
    "丝袜,网袜,过膝袜,瑜伽裤,打底裤,牛仔裤,短裤,阔腿裤,工装裤,运动裤,背带裤,连体裤,"
    "裙装,连衣裙,半身裙,百褶裙,包臀裙,吊带裙,旗袍,汉服,洛丽塔,JK制服,睡裙,礼服,婚纱,"
    "T恤,衬衫,卫衣,针织衫,毛衣,西装,套装,马甲,背心,吊带,露肩装,露脐装,"
    "大衣,风衣,羽绒服,皮衣,泳装,运动装,制服,睡衣,内衣,家居服,其他")
LEGACY_BODY_LABELS = (
    "大胸,小胸,长腿,高个,娇小,纤细,微胖,丰满,健美,"
    "细腰,翘臀,直角肩,锁骨,马甲线,腹肌,肌肉,长颈,沙漏型,梨形,苹果型")


def format_keywords(mapping: dict[str, list[str]]) -> str:
    """{标签: [词]} -> 「标签:词1,词2; 标签2:词」"""
    parts = []
    for label, words in mapping.items():
        words = [w for w in (words or []) if w and w != label]
        parts.append(f"{label}:" + ",".join(words) if words else label)
    return "; ".join(parts)

_TEMPLATE = r'''# DY Sort 配置文件
# 网页「设置」保存后也会写回本文件（保留注释）；修改后重启容器生效。
# 注意：yaml 值留空时，将回落使用环境变量（EMBY_URL 等）。

# Emby 服务器地址与 API Key（Emby 控制台 -> 高级 -> API 密钥）
emby_url: ""
emby_api_key: ""

# 只操作名称以该前缀开头的媒体库
emby_library_prefix: "DY_"

# 媒体库容量自动管理：加入 Emby 时以「博主目录」为最小单位分流，
# 视频数超过上限的库不再追加，改放到同名带序号的新库
# （DY_瑜伽裤 → DY_瑜伽裤2 → DY_瑜伽裤3，从不拆分同一个目录）
emby_auto_library: true

# 单个媒体库的视频数上限（自动管理开启时生效）
emby_library_max_videos: 2000

# ---------- 博主风格识别（图像识别） ----------
# 标签集合：逗号分隔，识别结果只会落在这里面的某个标签上（默认 49 个，越全越好但别太细）
style_labels: "丝袜,网袜,过膝袜,瑜伽裤,打底裤,牛仔裤,短裤,阔腿裤,工装裤,运动裤,背带裤,连体裤,裙装,连衣裙,半身裙,百褶裙,包臀裙,吊带裙,旗袍,汉服,洛丽塔,JK制服,睡裙,礼服,婚纱,T恤,衬衫,卫衣,针织衫,毛衣,西装,套装,马甲,背心,吊带,露肩装,露脐装,大衣,风衣,羽绒服,皮衣,高跟鞋,泳装,运动装,制服,睡衣,内衣,家居服,其他"
# 身材标签（第二个维度，默认 21 个）：每张图可命中多个，按命中张数排序
body_labels: "大胸,小胸,长腿,高个,娇小,纤细,微胖,丰满,有沟,健美,细腰,翘臀,直角肩,锁骨,马甲线,腹肌,肌肉,长颈,沙漏型,梨形,苹果型"

# 视觉模型：默认智谱 GLM-4V-Flash（免费图像理解模型），只需要填 API Key 即可用
# API Key 申请：https://bigmodel.cn/usercenter/proj-mgmt/apikeys
# 也可以换成任何 OpenAI 兼容的 /chat/completions 地址（OpenAI、DashScope、火山方舟、本地 vLLM 等）
vision_base_url: "https://open.bigmodel.cn/api/paas/v4"
vision_api_key: ""
vision_model: "glm-4v-flash"

# 每个博主随机抽几张海报（至少 10 张；素材不足时按实际数量）
vision_sample_videos: 10
# 每次请求塞几张图（1 = 每张图单独判断，最准也最贵）
vision_batch_images: 1
# 并发数
vision_concurrency: 2

# 识别引擎：local（默认，内置 CLIP 本地模型）| llm（云端大模型）| rules（关键词规则）
recognition_engine: "local"

# 内置本地模型（自动下载并在容器内推理，不需要 Ollama 之类的外部服务）
# 模型：CLIP ViT-B/32 ONNX；quantized 约 155MB，fp32 约 600MB
local_model_variant: "quantized"
local_model_dir: "/app/.cache/models"
local_model_mirror: "https://hf-mirror.com"
local_model_auto_download: true
# 输出粒度：coarse（大类，默认；CLIP 对同类细标签分辨力弱）| fine（细标签）
local_model_granularity: "coarse"

# 标签关键词：格式「标签:关键词1,关键词2; 标签2:关键词」
# 全新容器会自动写入内置同义词表（老配置升级时同样写入一次）；清空后保持为空
# 用于本地识别匹配，以及把模型输出的同义词归位到标准标签
label_keywords: ""
body_keywords: ""

# 绑定的短视频子目录：/data 下可能混有其他媒体，填写子目录名（如 "短视频"）
# 作为短视频根目录；留空 = 使用 /data 根。修改后索引会自动重建
media_subdir: ""

# Emby 侧视频根路径：当 Emby 看到的路径与容器内 /data 不一致时填写。
# 例：宿主机 D:\Videos 挂载为容器 /data，则填 host_video_root: 'D:\Videos'
# Windows 反斜杠路径请用单引号包裹，正斜杠路径双引号即可
host_video_root: ""

# 列表每页显示数量
page_size: 60

# 定时重新扫描间隔（分钟）：0 = 关闭；修改后立即生效，无需重启
scan_interval: 0

# 删除功能总开关：true 才允许删除视频/博主目录（默认关闭）。
# 开启后仍需二次确认；边界限制见 backend/cleaner.py
enable_delete: false
'''

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.width = 4096

_lock = threading.Lock()
_doc = None        # ruamel CommentedMap（保留注释元数据）
_file_keys: set[str] = set()   # 配置文件里**真正存在**的键（默认值填充的不算）
_loaded = False


def _ensure() -> None:
    global _doc, _loaded, _file_keys
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        created = not CONFIG_FILE.exists()
        if not created:
            try:
                _doc = _yaml.load(CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception:
                _doc = None
            # 解析失败时不覆盖用户文件，回落默认值
            if not isinstance(_doc, dict):
                _doc = {}
        else:
            CONFIG_FILE.write_text(_TEMPLATE, encoding="utf-8")
            _doc = _yaml.load(CONFIG_FILE.read_text(encoding="utf-8"))
        _file_keys = set(_doc.keys())
        # 留空的键回落环境变量默认值
        for k, v in _ENV_DEFAULTS.items():
            if _doc.get(k) in (None, ""):
                _doc[k] = v
        # 全新容器：识别关键词默认写入内置同义词表（与老配置升级时的迁移一致）；
        # 环境变量显式给了值则不覆盖。
        if created:
            changed = False
            for k, legacy in (("label_keywords", LEGACY_LABEL_KEYWORDS),
                              ("body_keywords", LEGACY_BODY_KEYWORDS)):
                if not str(_doc.get(k) or "").strip():
                    _doc[k] = format_keywords(legacy)
                    changed = True
            if changed:
                _write_locked()
        _loaded = True


def get(key: str, default: str = "") -> str:
    _ensure()
    v = _doc.get(key, default)
    return "" if v is None else str(v)


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(float(get(key) or default))
    except (TypeError, ValueError):
        return default


def get_float(key: str, default: float = 0.0) -> float:
    try:
        return float(get(key) or default)
    except (TypeError, ValueError):
        return default


def get_bool(key: str, default: bool = True) -> bool:
    v = get(key)
    if v == "":
        return default
    return v.lower() not in ("0", "false", "off", "no")


def _write_locked() -> None:
    with CONFIG_FILE.open("w", encoding="utf-8") as f:
        _yaml.dump(_doc, f)


def set_many(values: dict) -> None:
    """更新配置并写回 config.yaml（ruamel 保留注释）。

    配置文件里原本没有的键（老版本升级后新增的）即使值等于默认值也落盘一次，
    这样 yaml 里能看到并手工修改。"""
    _ensure()
    with _lock:
        changed = False
        for k, v in values.items():
            if k not in _ENV_DEFAULTS or v == "******":  # 掩码值不落盘
                continue
            if k in ("vision_base_url", "vision_model") and not str(v or "").strip():
                v = _ENV_DEFAULTS[k]   # 留空 = 用默认（智谱 GLM-4V-Flash）
            if _doc.get(k) != v or k not in _file_keys:
                _doc[k] = v
                _file_keys.add(k)
                changed = True
        if changed:
            _write_locked()


def persist_all() -> dict:
    """把配置里**缺失的键全部补齐并写入 config.yaml**，返回本次补写的键。

    - 所有键都落盘（包括空值），这样文件本身就是完整配置清单；
    - `label_keywords` / `body_keywords` 缺失时，写入旧版硬编码的同义词表
      （升级迁移：代码默认值为空，但已有行为不丢）；
    - `*api_key` 若原本只来自环境变量，则不落盘，避免把环境里的密钥写进文件。
    """
    _ensure()
    added: list[str] = []
    removed: list[str] = []
    migrated: list[str] = []
    with _lock:
        # 已废弃的键（上一版用过的本地外部服务配置）顺手清掉，避免误解
        for k in ("local_vision_base_url", "local_vision_api_key", "local_vision_model"):
            if k in _doc:
                del _doc[k]
                _file_keys.discard(k)
                removed.append(k)
        # 标签集升级迁移：恰好等于旧默认值的替换成新默认值（含高跟鞋/有沟），
        # 用户自定义过的列表保持不动。
        if _doc.get("style_labels") == LEGACY_STYLE_LABELS:
            _doc["style_labels"] = _ENV_DEFAULTS["style_labels"]
            _file_keys.add("style_labels")
            migrated.append("style_labels")
        if _doc.get("body_labels") == LEGACY_BODY_LABELS:
            _doc["body_labels"] = _ENV_DEFAULTS["body_labels"]
            _file_keys.add("body_labels")
            migrated.append("body_labels")
        for k, default in _ENV_DEFAULTS.items():
            if k in _file_keys:
                continue
            if k.endswith("api_key"):
                continue                      # 环境变量里的密钥不落盘
            if k == "label_keywords":
                value = format_keywords(LEGACY_LABEL_KEYWORDS)
            elif k == "body_keywords":
                value = format_keywords(LEGACY_BODY_KEYWORDS)
            else:
                value = _doc.get(k, default)
                if value is None:
                    value = default
            _doc[k] = value
            _file_keys.add(k)
            added.append(k)
        if added or removed or migrated:
            _write_locked()
    return {"added": added, "removed": removed, "migrated": migrated,
            "keys": len(_file_keys)}


def all_settings() -> dict:
    _ensure()
    out = {k: ("" if v is None else str(v)) for k, v in _doc.items()}
    # 所有 *api_key 只回传掩码，避免泄露
    for k in out:
        if k.endswith("api_key"):
            out[k] = "******" if _doc.get(k) else ""
    return out
