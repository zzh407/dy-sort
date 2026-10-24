"""博主穿衣风格识别：库名预标注 → 大模型识别（多标签） → 人工修正。

优先级：manual（人工） > vision（图像识别） > library（Emby 库名） > name（博主名关键词）。

识别方式：每个博主**随机抽 10 张以上海报**逐张判断，得到每个标签的命中次数，
按次数降序存成多标签（`丝袜×6、瑜伽裤×3`），主标签取第一名。

视觉后端是「OpenAI 兼容的 /chat/completions」：只要服务支持 image_url 就能用
（OpenAI、DashScope 兼容模式、火山方舟、智谱、本地 vLLM 等），不绑定具体厂商。
没有配置时，仍可用库名/博主名做零成本预标注，UI 上也能手工改。
"""
from __future__ import annotations

import base64
import json
import logging
import mimetypes
import random
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from . import db, settings

log = logging.getLogger("dy-sort.style")

DEFAULT_LABELS = [
    # 袜类
    "丝袜", "网袜", "过膝袜",
    # 裤装
    "瑜伽裤", "打底裤", "牛仔裤", "短裤", "阔腿裤", "工装裤", "运动裤", "背带裤", "连体裤",
    # 裙装
    "裙装", "连衣裙", "半身裙", "百褶裙", "包臀裙", "吊带裙",
    "旗袍", "汉服", "洛丽塔", "JK制服", "睡裙", "礼服", "婚纱",
    # 上装
    "T恤", "衬衫", "卫衣", "针织衫", "毛衣", "西装", "套装", "马甲",
    "背心", "吊带", "露肩装", "露脐装",
    # 外套
    "大衣", "风衣", "羽绒服", "皮衣",
    # 鞋履
    "高跟鞋",
    # 场景
    "泳装", "运动装", "制服", "睡衣", "内衣", "家居服",
    # 兜底
    "其他",
]
DEFAULT_BODY_LABELS = [
    "大胸", "小胸", "长腿", "高个", "娇小", "纤细", "微胖", "丰满", "有沟", "健美",
    "细腰", "翘臀", "直角肩", "锁骨", "马甲线", "腹肌", "肌肉", "长颈",
    "沙漏型", "梨形", "苹果型",
]

MIN_SAMPLE = 10          # 每个博主至少抽这么多张海报（素材不够时按实际数量）
DEFAULT_SAMPLE = 10
DEFAULT_BATCH = 1        # 每次请求几张图

# 识别引擎/人工写入的 source 值：库名、博主名预标注绝不能覆盖这些结果
RECOGNIZED_SOURCES = ("manual", "vision", "local-model", "local")

# 关键词（同义词）不再硬编码：全部来自 config.yaml 的 label_keywords / body_keywords，
# 默认空；升级时由 settings.persist_all() 把旧表一次性写进配置文件。

_TRAILING_DIGITS = re.compile(r"\d+$")

_state_lock = threading.Lock()
_state = {"running": False, "total": 0, "done": 0, "ok": 0, "failed": 0,
          "error": "", "current": "", "started_at": 0.0, "finished_at": 0.0,
          "images_total": 0, "images_done": 0, "sample": 0, "batch": 0}


class VisionError(Exception):
    pass


# ---------- 标签 ----------
def labels() -> list[str]:
    raw = settings.get("style_labels") or ""
    out = [x.strip() for x in re.split(r"[,，、;；\s]+", raw) if x.strip()]
    return out or list(DEFAULT_LABELS)


def body_labels() -> list[str]:
    raw = settings.get("body_labels") or ""
    out = [x.strip() for x in re.split(r"[,，、;；\s]+", raw) if x.strip()]
    return out or list(DEFAULT_BODY_LABELS)


def keyword_map(kind: str = "style") -> dict[str, list[str]]:
    """解析配置里的标签关键词：`丝袜:黑丝,连裤袜; 瑜伽裤:瑜伽`。

    只保留标签集合里存在的标签；默认空 = 不做同义词归位。"""
    raw = settings.get("body_keywords" if kind == "body" else "label_keywords") or ""
    labs = body_labels() if kind == "body" else labels()
    out: dict[str, list[str]] = {}
    for chunk in re.split(r"[;\n；]+", raw):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" in chunk or "：" in chunk:
            label, words = re.split(r"[:：]", chunk, maxsplit=1)
        else:
            label, words = chunk, ""
        label = label.strip()
        if label not in labs:
            continue
        kw = [w.strip() for w in re.split(r"[,，、\s]+", words) if w.strip()]
        if kw:
            out.setdefault(label, []).extend(kw)
    return out


def _match(value: str, labs: list[str], kws: dict[str, list[str]]) -> str:
    v = (value or "").strip().strip("。.，,\"'` ")
    if not v:
        return ""
    if v in labs:
        return v
    try:  # 本地模型的粗粒度大类也要能原样保留
        from . import localmodel
        if v in localmodel.COARSE_LABELS:
            return v
    except Exception:  # noqa: BLE001
        pass
    low = v.lower()
    for lab in labs:
        if lab in v or v in lab:
            return lab
    for lab, keys in kws.items():
        for k in keys:
            if k and (k in v or k.lower() in low):
                return lab
    return ""


def normalize(value: str) -> str:
    """把模型/用户的输入规整成标签集合里的某一个；认不出来返回空串。"""
    return _match(value, labels(), keyword_map("style"))


def normalize_body(value: str) -> str:
    """身材标签规整；认不出来返回空串。"""
    v = (value or "").strip()
    if v in ("无", "不判断", "看不清", "无身材特征"):
        return ""
    return _match(v, body_labels(), keyword_map("body"))


def body_from_name(name: str) -> list[str]:
    """从博主名里猜身材标签（如「长腿小姐姐」→ 长腿），只做保守匹配。"""
    raw = (name or "").strip()
    if not raw:
        return []
    labs = body_labels()
    out = [lab for lab in labs if lab in raw]
    if out:
        return out
    kws = keyword_map("body")
    low = raw.lower()
    for lab, keys in kws.items():
        if any(k and (k in raw or k.lower() in low) for k in keys):
            out.append(lab)
    return out


def label_from_name(name: str, prefix: str = "") -> str:
    """从库名或博主名里猜标签：DY_瑜伽裤2 → 瑜伽裤；厚丝小阿姨 → 丝袜。"""
    raw = (name or "").strip()
    if prefix and raw.startswith(prefix):
        raw = raw[len(prefix):]
    raw = _TRAILING_DIGITS.sub("", raw)
    if not raw:
        return ""
    labs = labels()
    if raw in labs:
        return raw
    for lab in labs:
        if raw == lab or raw.startswith(lab) or raw.endswith(lab):
            return lab
    kws = keyword_map("style")
    low = raw.lower()
    for lab, keys in kws.items():
        if any(k and (k in raw or k.lower() in low) for k in keys):
            return lab
    return ""


# ---------- 存取 ----------
def _dump(labels_list: list[dict]) -> str:
    return json.dumps(labels_list or [], ensure_ascii=False)


def _load(raw: str) -> list[dict]:
    try:
        data = json.loads(raw or "[]")
    except Exception:  # noqa: BLE001
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        style = str(item.get("style") or "")
        if not style:
            continue
        out.append({"style": style,
                    "count": int(item.get("count") or 0),
                    "ratio": float(item.get("ratio") or 0)})
    return out


def get_styles() -> dict[str, dict]:
    rows = db.query("SELECT blogger, style, labels, body, body_labels, source, "
                    "confidence, note, updated_at FROM blogger_style "
                    "WHERE style IS NOT NULL AND style != '' "
                    "   OR body IS NOT NULL AND body != ''")
    return {r["blogger"]: {"style": r["style"], "labels": _load(r["labels"]),
                           "body": r["body"], "body_labels": _load(r["body_labels"]),
                           "source": r["source"], "confidence": r["confidence"],
                           "note": r["note"], "updated_at": r["updated_at"]}
            for r in rows}


def get_style(blogger: str) -> dict:
    row = db.query_one("SELECT style, labels, body, body_labels, source, confidence, "
                       "note, updated_at FROM blogger_style WHERE blogger=?", (blogger,))
    if row is None or not (row["style"] or row["body"]):
        return {"style": "", "labels": [], "body": "", "body_labels": [],
                "source": "", "confidence": 0, "note": "", "updated_at": 0}
    labels_list = _load(row["labels"])
    if not labels_list and row["style"]:
        labels_list = [{"style": row["style"], "count": 1, "ratio": 1.0}]
    body_list = _load(row["body_labels"])
    if not body_list and row["body"]:
        body_list = [{"style": row["body"], "count": 1, "ratio": 1.0}]
    return {"style": row["style"], "labels": labels_list,
            "body": row["body"], "body_labels": body_list,
            "source": row["source"], "confidence": row["confidence"],
            "note": row["note"], "updated_at": row["updated_at"]}


def set_style(blogger: str, style: str, source: str, confidence: float = 0.0,
              note: str = "", labels_list: list[dict] | None = None,
              body: str = "", body_labels_list: list[dict] | None = None) -> None:
    if labels_list is None:
        labels_list = ([{"style": style, "count": 1, "ratio": 1.0}] if style else [])
    if body_labels_list is None:
        body_labels_list = ([{"style": body, "count": 1, "ratio": 1.0}] if body else [])
    if not body and body_labels_list:
        body = body_labels_list[0]["style"]
    db.execute(
        "INSERT INTO blogger_style(blogger, style, labels, body, body_labels, source, "
        "confidence, note, updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(blogger) DO UPDATE SET "
        "style=excluded.style, labels=excluded.labels, body=excluded.body, "
        "body_labels=excluded.body_labels, source=excluded.source, "
        "confidence=excluded.confidence, note=excluded.note, "
        "updated_at=excluded.updated_at",
        (blogger, style, _dump(labels_list), body, _dump(body_labels_list), source,
         float(confidence or 0), note, time.time()))


def clear_style(blogger: str) -> None:
    db.execute("DELETE FROM blogger_style WHERE blogger=?", (blogger,))


def set_style_only(blogger: str, style: str, source: str, confidence: float = 0.0,
                   note: str = "", labels_list: list[dict] | None = None) -> None:
    """只写风格维度，保留已有的身材标签（库名/博主名预标注用）。"""
    if labels_list is None:
        labels_list = ([{"style": style, "count": 1, "ratio": 1.0}] if style else [])
    db.execute(
        "INSERT INTO blogger_style(blogger, style, labels, source, confidence, note, updated_at) "
        "VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(blogger) DO UPDATE SET "
        "style=excluded.style, labels=excluded.labels, source=excluded.source, "
        "confidence=excluded.confidence, note=excluded.note, updated_at=excluded.updated_at",
        (blogger, style, _dump(labels_list), source, float(confidence or 0), note,
         time.time()))


# ---------- 零成本预标注：Emby 库名 / 博主名 ----------
def seed_from_libraries() -> int:
    """按 Emby 库名给博主预标注（DY_丝袜 → 丝袜）；不覆盖人工与已有的识别结果。"""
    prefix = settings.get("emby_library_prefix", "DY_") or "DY_"
    rows = db.query(
        "SELECT m.blogger AS blogger, m.library AS library, s.source AS source, "
        "       s.style AS style "
        "FROM emby_map m LEFT JOIN blogger_style s ON s.blogger = m.blogger "
        "WHERE m.library IS NOT NULL AND m.library != ''")
    done: set[str] = set()
    changed = 0
    for r in rows:
        blogger = r["blogger"]
        if blogger in done or (r["source"] in RECOGNIZED_SOURCES and r["style"]):
            continue
        lab = label_from_name(r["library"], prefix)
        if not lab:
            continue
        if r["style"] == lab and r["source"] == "library":
            done.add(blogger)
            continue
        set_style_only(blogger, lab, "library", 0.9, f"来自媒体库名「{r['library']}」")
        done.add(blogger)
        changed += 1
    if changed:
        log.info("按库名预标注风格: %d 个博主", changed)
    return changed


def seed_from_blogger_names() -> int:
    """库名没覆盖到的，再用博主名关键词猜一次（置信度较低）。"""
    rows = db.query(
        "SELECT b.dir_name AS blogger, s.style AS style, s.source AS source "
        "FROM bloggers b LEFT JOIN blogger_style s ON s.blogger = b.dir_name")
    changed = 0
    for r in rows:
        if r["style"] or r["source"] in RECOGNIZED_SOURCES:
            continue
        lab = label_from_name(r["blogger"])
        if not lab:
            continue
        set_style_only(r["blogger"], lab, "name", 0.5, "来自博主名关键词")
        changed += 1
    if changed:
        log.info("按博主名预标注风格: %d 个", changed)
    return changed


def seed_body_from_names() -> int:
    """按博主名猜身材标签（「长腿小姐姐」→ 长腿），只补空的身材维度。"""
    rows = db.query(
        "SELECT b.dir_name AS blogger, COALESCE(s.body,'') AS body "
        "FROM bloggers b LEFT JOIN blogger_style s ON s.blogger = b.dir_name")
    changed = 0
    for r in rows:
        if r["body"]:
            continue
        labs = body_from_name(r["blogger"])
        if not labs:
            continue
        cur = get_style(r["blogger"])
        body_list = [{"style": lab, "count": 1, "ratio": 1.0} for lab in labs]
        set_style(r["blogger"], cur["style"], cur["source"] or "name",
                  cur["confidence"], cur["note"] or "来自博主名关键词",
                  labels_list=cur["labels"], body=labs[0],
                  body_labels_list=body_list)
        changed += 1
    if changed:
        log.info("按博主名预标注身材: %d 个", changed)
    return changed


def summary() -> list[dict]:
    rows = db.query("SELECT style, COUNT(*) c FROM blogger_style "
                    "WHERE style IS NOT NULL AND style != '' GROUP BY style "
                    "ORDER BY c DESC")
    return [{"style": r["style"], "count": r["c"]} for r in rows]


def body_summary() -> list[dict]:
    rows = db.query("SELECT body, COUNT(*) c FROM blogger_style "
                    "WHERE body IS NOT NULL AND body != '' GROUP BY body "
                    "ORDER BY c DESC")
    return [{"style": r["body"], "count": r["c"]} for r in rows]


def blogger_page(q: str = "", style: str = "", page: int = 1,
                 page_size: int = 200) -> dict:
    """博主列表（含风格 + 身材多标签），按视频数降序。"""
    where, args = [], []
    if q:
        where.append("(b.dir_name LIKE ? OR s.style LIKE ? OR s.labels LIKE ? "
                     "OR s.body LIKE ? OR s.body_labels LIKE ?)")
        args += [f"%{q}%"] * 5
    if style:
        where.append("(COALESCE(s.style,'') = ? OR COALESCE(s.body,'') = ?)")
        args += [style, style]
    w = ("WHERE " + " AND ".join(where)) if where else ""
    total = db.query_one(
        f"SELECT COUNT(*) c FROM bloggers b LEFT JOIN blogger_style s "
        f"ON s.blogger = b.dir_name {w}", tuple(args))["c"]
    rows = db.query(
        f"SELECT b.dir_name AS blogger, b.video_count AS videos, b.in_emby AS in_emby, "
        f"       b.poster_key AS poster_key, COALESCE(s.style,'') AS style, "
        f"       COALESCE(s.labels,'[]') AS labels, COALESCE(s.body,'') AS body, "
        f"       COALESCE(s.body_labels,'[]') AS body_labels, "
        f"       COALESCE(s.source,'') AS source, "
        f"       COALESCE(s.note,'') AS note, "
        f"       (SELECT v.id FROM videos v WHERE v.blogger_id = b.id "
        f"        ORDER BY v.name LIMIT 1) AS first_video "
        f"FROM bloggers b LEFT JOIN blogger_style s ON s.blogger = b.dir_name {w} "
        f"ORDER BY b.video_count DESC, b.dir_name LIMIT ? OFFSET ?",
        tuple(args) + (page_size, (page - 1) * page_size))
    items = []
    for r in rows:
        if r["poster_key"]:
            poster = f"/api/blogger/{r['blogger']}/poster"
        elif r["first_video"]:
            poster = f"/api/video/{r['first_video']}/poster"   # 没有博主级海报就借首条视频的
        else:
            poster = None
        labels_list = _load(r["labels"])
        if not labels_list and r["style"]:
            # 老数据（只有单标签）补一条明细，前端统一按多标签渲染
            labels_list = [{"style": r["style"], "count": 1, "ratio": 1.0}]
        body_list = _load(r["body_labels"])
        if not body_list and r["body"]:
            body_list = [{"style": r["body"], "count": 1, "ratio": 1.0}]
        items.append({"blogger": r["blogger"], "videos": r["videos"],
                      "in_emby": bool(r["in_emby"]), "style": r["style"],
                      "labels": labels_list, "body": r["body"],
                      "body_labels": body_list, "source": r["source"],
                      "note": r["note"], "poster": poster})
    return {"total": total, "page": page, "page_size": page_size,
            "labels": labels(), "body_labels": body_labels(), "items": items,
            "summary": summary(), "body_summary": body_summary()}


# ---------- 识别引擎（本地模型 / 大模型 / 关键词规则） ----------
def engine() -> str:
    """设置里的识别引擎：local（默认，内置 CLIP）| llm（云端）| rules（关键词）。"""
    v = (settings.get("recognition_engine") or "local").strip().lower()
    return v if v in ("local", "llm", "rules") else "local"


def vision_configured() -> bool:
    """大模型（云端 OpenAI 兼容）是否可用。

    默认地址是智谱，必须填 API Key；用户自填的其他兼容地址允许无 Key（本地 vLLM 等）。"""
    base = settings.get("vision_base_url").strip()
    model = settings.get("vision_model").strip()
    key = settings.get("vision_api_key").strip()
    if not base or not model:
        return False
    if not key and "bigmodel.cn" in base:
        return False
    return True


def local_model_ready() -> bool:
    from . import localmodel
    try:
        return localmodel.model_ready()
    except Exception:  # noqa: BLE001
        return False


def engine_ready() -> bool:
    """本地模型可自动下载，规则引擎直接可用；只有大模型必须预先配置。"""
    return vision_configured() if engine() == "llm" else True


def engine_label() -> str:
    e = engine()
    if e == "llm":
        return "大模型"
    if e == "rules":
        return "本地关键词规则"
    return "本地模型（内置 CLIP）"


class LocalModelClient:
    """把内置 CLIP 适配成与 VisionClient 相同的 classifiy_many 接口。

    模型文件不存在时会自动下载（进度通过 state 的 download_* 字段暴露）。"""

    def __init__(self, progress=None):
        from . import localmodel
        self._lm = localmodel.get_model(progress)
        self.base = ""
        self.model = localmodel.model_name()
        self.key = ""
        self.kind = "local-model"

    def classify_many(self, images: list[tuple[bytes, str]],
                      labs: list[str] | None = None,
                      bodies: list[str] | None = None) -> list[dict]:
        labs = labs or labels()
        bodies = bodies or body_labels()
        out: list[dict] = []
        for raw, _mime in images:
            try:
                r = self._lm.classify(raw, labs, bodies)
            except Exception as e:  # noqa: BLE001
                raise VisionError(f"本地模型推理失败: {e}")
            out.append({"style": r["style"], "body": r["body"],
                        "confidence": r["confidence"], "note": r["note"]})
        return out


class VisionClient:
    """OpenAI 兼容的 /chat/completions 客户端（云端大模型，多图 + 逐图标签）。"""

    def __init__(self, kind: str = "llm"):
        self.kind = "llm"
        base = settings.get("vision_base_url").strip().rstrip("/")
        model = settings.get("vision_model").strip()
        self.key = settings.get("vision_api_key").strip()
        where = "大模型"
        if not base:
            raise VisionError(f"未配置{where}模型地址（设置 → 图像识别）")
        if not model:
            raise VisionError(f"未配置{where}模型名（设置 → 图像识别）")
        if "bigmodel.cn" in base and not self.key:
            raise VisionError("未配置智谱 API Key（设置 → 图像识别 → 大模型 API Key）")
        self.base = base
        self.model = model

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.key:
            h["Authorization"] = f"Bearer {self.key}"
        return h

    def classify_many(self, images: list[tuple[bytes, str]],
                      labs: list[str] | None = None,
                      bodies: list[str] | None = None) -> list[dict]:
        """一次请求识别多张图，返回与 images 等长的结果列表（风格 + 身材）。"""
        labs = labs or labels()
        bodies = bodies or body_labels()
        if not images:
            return []
        content = [{"type": "text", "text": _prompt(labs, bodies, len(images))}]
        for raw, mime in images:
            b64 = base64.b64encode(raw).decode("ascii")
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"}})
        payload = {"model": self.model, "temperature": 0, "max_tokens": 600,
                   "messages": [
                       {"role": "system",
                        "content": "你是服装风格标注助手，只输出 JSON，不要解释、不要代码块。"},
                       {"role": "user", "content": content}]}
        last = ""
        for attempt in range(2):
            try:
                r = httpx.post(self.base + "/chat/completions", headers=self._headers(),
                               json=payload, timeout=180)
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {e}"
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}: {(r.text or '')[:160]}"
                time.sleep(2.0 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise VisionError(f"视觉模型返回 HTTP {r.status_code}："
                                  f"{(r.text or '').strip()[:200]}")
            try:
                data = r.json()
                text = data["choices"][0]["message"]["content"]
            except Exception as e:  # noqa: BLE001
                raise VisionError(f"视觉模型返回结构异常: {e}")
            if isinstance(text, list):
                text = "".join(part.get("text", "") for part in text
                               if isinstance(part, dict))
            return _parse_many(text or "", len(images))
        raise VisionError(f"视觉模型请求失败：{last}")


_STYLE_GUIDE = (
    "袜类：丝袜=黑丝/肉丝；网袜=渔网袜；过膝袜=长筒袜。"
    "裤装：瑜伽裤/打底裤=紧身裤；牛仔裤=牛仔面料；短裤=热裤；阔腿裤=裤腿宽大；"
    "工装裤=多口袋；运动裤=卫裤；背带裤；连体裤。"
    "裙装：裙装=说不清款式；连衣裙=连身；半身裙=分体裙；百褶裙；包臀裙；吊带裙；"
    "旗袍；汉服=古装；洛丽塔=lo裙；JK制服=JK校服；睡裙=睡袍；礼服=晚礼服；婚纱。"
    "上装：T恤=短袖T；衬衫=衬衣；卫衣=帽衫；针织衫=开衫；毛衣；西装=西服外套；"
    "套装=成套；马甲=背心外套；背心=无袖；吊带=吊带衫；露肩装=一字肩/抹胸；露脐装=短上衣。"
    "外套：大衣=呢子；风衣；羽绒服；皮衣。"
    "鞋类：高跟鞋=高跟/细跟/尖头高跟鞋。"
    "场景：泳装=比基尼/泳衣；运动装=运动背心/球衣/健身服；制服=护士/空姐/学生装；"
    "睡衣=成套睡衣；内衣=文胸；家居服=居家服。其他=都不符合。"
)
_BODY_GUIDE = (
    "大胸=胸部明显突出；小胸=胸部平缓；长腿=腿部修长；高个=整体偏高；娇小=身材娇小；"
    "纤细=整体偏瘦；微胖=有肉但不夸张；丰满=曲线明显；健美=有运动感的匀称身材；"
    "细腰=腰身纤细；翘臀=臀部曲线明显；直角肩=肩线平直；锁骨=锁骨明显；"
    "马甲线=腹部有两条竖线；腹肌=腹部分块；肌肉=四肢/肩背有明显训练痕迹；"
    "长颈=颈部修长；有沟=领口可见乳沟；沙漏型=肩臀宽腰细；梨形=臀腿明显宽于肩；苹果型=腰腹偏圆。"
)


def _prompt(labs: list[str], bodies: list[str], count: int) -> str:
    common = (
        "对每张图判断两件事：\n"
        "① 穿衣风格：必须从下列标签中选一个："
        f"{'、'.join(labs)}。\n"
        f"   判断参考：{_STYLE_GUIDE}\n"
        "② 身材特征：从下列标签中选 0~2 个（确实看得出来才选，看不出来就空数组）："
        f"{'、'.join(bodies)}。\n"
        f"   判断参考：{_BODY_GUIDE}\n"
        "③ 如果这张图没有任何有效内容（纯色块、纯黑/纯白、模糊到看不清、明显不是人物画面），"
        '则 style 返回 "无内容"、body 返回空数组，不要硬猜。\n'
        "只描述画面可观察的特征，不要臆测，也不要输出标签以外的词。\n"
    )
    if count <= 1:
        return common + (
            '只输出 JSON：{"style":"风格标签或 无内容","body":["身材标签"],'
            '"confidence":0.0到1.0,"reason":"不超过20字"}')
    return common + (
        f"图一共 {count} 张（按顺序编号 1..{count}），请逐张判断，"
        f"输出 JSON 数组，必须包含 {count} 项，每项形如 "
        '{"index":1,"style":"风格标签或 无内容","body":["身材标签"],"confidence":0.0,"reason":"不超过10字"}。'
        "不要输出数组以外的任何内容。")


def _norm_body_list(value) -> list[str]:
    """把模型给的身材字段（字符串或数组）规整成去重后的标签列表。"""
    raw = value if isinstance(value, list) else ([] if value is None else [value])
    out: list[str] = []
    for item in raw:
        lab = normalize_body(str(item))
        if lab and lab not in out:
            out.append(lab)
    return out


def _parse_single(raw: str) -> dict:
    obj = None
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(raw[start:end + 1])
        except Exception:  # noqa: BLE001
            obj = None
    if not isinstance(obj, dict):
        m = re.search(r'"style"\s*:\s*"([^"]+)"', raw)
        obj = {"style": m.group(1) if m else raw[:60]}
    style = normalize(str(obj.get("style", "")))
    try:
        conf = float(obj.get("confidence", 0))
    except (TypeError, ValueError):
        conf = 0.0
    return {"style": style, "body": _norm_body_list(obj.get("body")),
            "confidence": max(0.0, min(1.0, conf)),
            "note": str(obj.get("reason", "") or "")[:60], "raw": raw[:200]}


def _parse_many(raw: str, count: int) -> list[dict]:
    """解析逐图结果；模型只给一个结论时套用到所有图。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    items = None
    # 只有整段以 [ 开头才当数组解析，否则 {"body":[...]} 里的方括号会误判
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                items = parsed
        except Exception:  # noqa: BLE001
            start, end = text.find("["), text.rfind("]")
            if start >= 0 and end > start:
                try:
                    parsed = json.loads(text[start:end + 1])
                    if isinstance(parsed, list):
                        items = parsed
                except Exception:  # noqa: BLE001
                    items = None
    if items:
        out: list[dict] = []
        for i in range(count):
            entry = items[i] if i < len(items) and isinstance(items[i], dict) else {}
            style = normalize(str(entry.get("style", "")))
            try:
                conf = float(entry.get("confidence", 0))
            except (TypeError, ValueError):
                conf = 0.0
            out.append({"style": style, "body": _norm_body_list(entry.get("body")),
                        "confidence": max(0.0, min(1.0, conf)),
                        "note": str(entry.get("reason", "") or "")[:60]})
        return out
    single = _parse_single(text)
    return [dict(single) for _ in range(count)]


# ---------- 采样与批量识别 ----------
_stop = threading.Event()


def _read_image(path: str) -> tuple[bytes, str] | None:
    try:
        with open(path, "rb") as f:
            raw = f.read(8 * 1024 * 1024)
    except OSError:
        return None
    if not raw:
        return None
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    if not mime.startswith("image/"):
        mime = "image/jpeg"
    return raw, mime


def _is_blank(raw: bytes) -> bool:
    """纯色/纯黑/纯白/几乎没有细节的图：本地先筛掉，省一次模型请求。"""
    try:
        from io import BytesIO

        from PIL import Image, ImageStat
        with Image.open(BytesIO(raw)) as im:
            gray = im.convert("L")
            gray.thumbnail((64, 64))
            stat = ImageStat.Stat(gray)
            mean = stat.mean[0] if stat.mean else 0.0
            std = stat.stddev[0] if stat.stddev else 0.0
        return std < 5.0 or mean < 6 or mean > 249
    except Exception:  # noqa: BLE001 - 解不开就交给模型判断
        return False


def _poster_paths(blogger: str) -> list[str]:
    """该博主所有可用海报路径（随机打乱，便于抽样与补图）。"""
    rows = db.query(
        "SELECT v.poster AS poster FROM videos v JOIN bloggers b ON b.id = v.blogger_id "
        "WHERE b.dir_name = ? AND v.poster IS NOT NULL AND v.poster != ''", (blogger,))
    paths = [r["poster"] for r in rows]
    random.shuffle(paths)
    return paths


def _targets(bloggers: list[str] | None, force: bool) -> list[str]:
    sql = ("SELECT b.dir_name AS blogger FROM bloggers b "
           "LEFT JOIN blogger_style s ON s.blogger = b.dir_name ")
    where, args = [], []
    if bloggers:
        where.append("b.dir_name IN (" + ",".join("?" * len(bloggers)) + ")")
        args += list(bloggers)
    if not force:
        where.append("(s.style IS NULL OR s.style = '')")
    # 人工标注过的不覆盖
    where.append("COALESCE(s.source,'') != 'manual'")
    if where:
        sql += "WHERE " + " AND ".join(where)
    sql += " ORDER BY b.video_count DESC, b.dir_name"
    return [r["blogger"] for r in db.query(sql, tuple(args))]


def _estimate_images(targets: list[str], sample: int | None) -> int:
    """预计要判多少张图：sample=None 表示全量（每个博主的全部视频）。"""
    counts = {r["dir_name"]: int(r["video_count"] or 0)
              for r in db.query("SELECT dir_name, video_count FROM bloggers")}
    total = 0
    for b in targets:
        v = max(0, counts.get(b, 0))
        total += v if sample is None else min(sample, v)
    return total


def _classify_blogger(blogger: str, client: VisionClient, need: int,
                      batch: int, labs: list[str], bodies: list[str]
                      ) -> tuple[list[dict] | None, list[dict] | None, str, int]:
    """识别一个博主：抽 need 张有效图；空白/无内容的图自动换下一张。

    返回 (风格多标签, 身材多标签, 备注, 跳过的无效图数)。
    """
    pool = _poster_paths(blogger)
    if not pool:
        return None, None, "没有海报", 0
    style_counter: Counter = Counter()
    body_counter: Counter = Counter()
    notes: list[str] = []
    queue: list[tuple[bytes, str]] = []
    good = 0
    skipped = 0

    def bump_skip(n: int = 1) -> None:
        nonlocal skipped
        skipped += n
        with _state_lock:
            _state["skipped_blank"] = _state.get("skipped_blank", 0) + n

    def fill() -> None:
        """从池子里取出本地校验通过的图补满一个 batch。"""
        while pool and len(queue) < batch:
            img = _read_image(pool.pop(0))
            if not img:
                continue
            if _is_blank(img[0]):
                bump_skip()
                continue
            queue.append(img)

    fill()
    while queue and good < need and not _stop.is_set():
        chunk = queue[:batch]
        del queue[:batch]
        results = client.classify_many(chunk, labs, bodies)
        for res in results:
            if res["style"]:
                style_counter[res["style"]] += 1
            for body in res.get("body") or []:
                body_counter[body] += 1
            if res["style"] or (res.get("body") or []):
                good += 1
                if res.get("note"):
                    notes.append(res["note"])
            else:
                bump_skip()        # 模型说「无内容」→ 换一张再试
        with _state_lock:
            _state["images_done"] += len(chunk)
        fill()

    style_total = sum(style_counter.values())
    body_total = sum(body_counter.values())
    if not style_total and not body_total:
        return None, None, f"采样 {good} 张均无法归类（跳过 {skipped} 张）", skipped
    style_list = [{"style": s, "count": c, "ratio": round(c / style_total, 3)}
                  for s, c in style_counter.most_common()] if style_total else []
    body_list = [{"style": s, "count": c, "ratio": round(c / body_total, 3)}
                 for s, c in body_counter.most_common()] if body_total else []
    note = (f"采样 {good} 张，风格 {style_total} 张有结论、身材 {body_total} 次命中"
            + (f"，跳过 {skipped} 张无内容" if skipped else ""))
    if notes:
        note += f"；{notes[0]}"
    return style_list, body_list, note, skipped


def run_batch(bloggers: list[str] | None = None, force: bool = False,
              mode: str = "quick", sample: int | None = None,
              batch: int | None = None) -> dict:
    """后台批量识别。

    mode="quick"：每个博主随机抽 10 张（可用 sample 覆盖，至少 10）；
    mode="full" ：识别每个博主的**全部视频**。

    引擎由 recognition_engine 决定：
    - local：配置了本地视觉模型就走图片；否则用「标签关键词 + 库名/博主名」规则；
    - llm  ：云端大模型（vision_* 那组配置）。
    """
    global _state
    mode = (mode or "quick").lower()
    if mode not in ("quick", "full"):
        mode = "quick"
    eng = engine()
    with _state_lock:
        if _state["running"]:
            return {"started": False, "error": "识别任务已在进行中",
                    "total": _state["total"]}
        if eng == "llm" and not vision_configured():
            return {"started": False,
                    "error": "当前引擎是「大模型」，但未配置大模型地址/模型名（设置 → 图像识别）",
                    "total": 0}
        rules_only = (eng == "rules")
        targets = _targets(bloggers, force)
        n_sample = None if mode == "full" else max(
            MIN_SAMPLE, int(sample or settings.get_int(
                "vision_sample_videos", DEFAULT_SAMPLE) or DEFAULT_SAMPLE))
        n_batch = max(1, min(8, int(batch or settings.get_int(
            "vision_batch_images", DEFAULT_BATCH) or DEFAULT_BATCH)))
        _stop.clear()
        _state = {"running": True, "total": len(targets), "done": 0, "ok": 0,
                  "failed": 0, "error": "", "current": "", "started_at": time.time(),
                  "finished_at": 0.0, "mode": mode, "sample": n_sample or 0,
                  "batch": n_batch, "engine": eng,
                  "engine_label": engine_label(),
                  "images_total": (len(targets) if rules_only
                                   else _estimate_images(targets, n_sample)),
                  "images_done": 0, "skipped_blank": 0, "skipped": 0,
                  "download_done": 0, "download_total": 0, "download_text": ""}
    if not targets:
        with _state_lock:
            _state["running"] = False
            _state["finished_at"] = time.time()
        return {"started": False, "error": "没有需要识别的博主", "total": 0,
                "mode": mode, "engine": eng}
    if rules_only:
        threading.Thread(target=_rules_worker, args=(targets,), daemon=True,
                         name="style-rules").start()
    else:
        threading.Thread(target=_worker, args=(targets, n_sample, n_batch), daemon=True,
                         name="style-vision").start()
    return {"started": True, "total": len(targets), "error": "", "mode": mode,
            "sample": n_sample or 0, "batch": n_batch, "engine": eng,
            "engine_label": engine_label()}


def stop() -> dict:
    """请求停止当前识别任务（已提交的图片跑完就收尾）。"""
    if not _state.get("running"):
        return {"stopped": False, "error": "没有正在运行的识别任务"}
    _stop.set()
    with _state_lock:
        _state["current"] = "正在停止…"
    return {"stopped": True}


def _rule_style_from_videos(blogger: str, limit: int = 8) -> str:
    """从该博主几条随机视频名里找风格关键词（视频标题经常带「丝袜/瑜伽裤」）。"""
    rows = db.query(
        "SELECT v.name AS name FROM videos v JOIN bloggers b ON b.id = v.blogger_id "
        "WHERE b.dir_name = ? ORDER BY RANDOM() LIMIT ?", (blogger, limit))
    for r in rows:
        lab = label_from_name(r["name"] or "")
        if lab:
            return lab
    return ""


def _download_progress(kind: str, done: int, total: int) -> None:
    with _state_lock:
        _state["download_done"] = done
        _state["download_total"] = total
        _state["download_text"] = (f"下载本地模型 {done / 1e6:.0f}/"
                                   f"{total / 1e6:.0f} MB")


def _rules_worker(targets: list[str]) -> None:
    """本地关键词规则识别：不读图片、不联网，按 Emby 库名 / 博主名 / 视频名 / 标签关键词打标。"""
    global _state
    prefix = settings.get("emby_library_prefix", "DY_") or "DY_"
    libs: dict[str, str] = {}
    for r in db.query("SELECT blogger, library FROM emby_map "
                      "WHERE library IS NOT NULL AND library != '' ORDER BY added_at"):
        libs.setdefault(r["blogger"], r["library"])
    for blogger in targets:
        if _stop.is_set():
            break
        lib = libs.get(blogger, "")
        style = (label_from_name(lib, prefix) or label_from_name(blogger)
                 or _rule_style_from_videos(blogger))
        bodies = body_from_name(blogger)
        hit = bool(style or bodies)
        if hit:
            labels_list = ([{"style": style, "count": 1, "ratio": 1.0}] if style else [])
            body_list = [{"style": b, "count": 1, "ratio": 1.0} for b in bodies]
            note = "本地关键词规则"
            if lib:
                note += f"（库名 {lib}）"
            set_style(blogger, style, "local", 0.8 if style else 0.0, note,
                      labels_list=labels_list,
                      body=bodies[0] if bodies else "", body_labels_list=body_list)
        with _state_lock:
            _state["done"] += 1
            _state["images_done"] += 1
            _state["current"] = f"{blogger} → " + (
                "、".join(([style] if style else []) + bodies) if hit else "未命中关键词")
            if hit:
                _state["ok"] += 1
            else:
                _state["skipped"] = _state.get("skipped", 0) + 1
    with _state_lock:
        _state["running"] = False
        _state["finished_at"] = time.time()
        if _stop.is_set() and not _state["error"]:
            _state["error"] = "已手动停止"
    log.info("本地关键词识别结束: 命中 %d，未命中 %d", _state["ok"], _state.get("skipped", 0))


def _worker(targets: list[str], sample: int | None, batch: int) -> None:
    global _state
    conc = max(1, min(8, settings.get_int("vision_concurrency", 2) or 2))
    labs = labels()
    bodies = body_labels()
    counts = {r["dir_name"]: int(r["video_count"] or 0)
              for r in db.query("SELECT dir_name, video_count FROM bloggers")}
    try:
        client = (LocalModelClient(progress=_download_progress)
                  if engine() == "local" else VisionClient("llm"))
    except Exception as e:  # noqa: BLE001 - VisionError / 下载失败 / 缺依赖
        with _state_lock:
            _state.update(running=False, error=str(e), finished_at=time.time())
        log.warning("风格识别未启动: %s", e)
        return

    def one(blogger: str) -> tuple[bool, str]:
        if _stop.is_set():
            return False, "已停止"
        need = sample if sample else max(1, counts.get(blogger, 0))
        style_list, body_list, note, _skipped = _classify_blogger(
            blogger, client, need, batch, labs, bodies)
        if not style_list and not body_list:
            return False, note
        top_style = style_list[0]["style"] if style_list else ""
        top_body = body_list[0]["style"] if body_list else ""
        src = "local-model" if engine() == "local" else "vision"
        set_style(blogger, top_style, src,
                  style_list[0]["ratio"] if style_list else 0.0, note,
                  labels_list=style_list, body=top_body,
                  body_labels_list=body_list)
        parts = []
        if style_list:
            parts.append("风格 " + "、".join(f"{x['style']}×{x['count']}" for x in style_list[:3]))
        if body_list:
            parts.append("身材 " + "、".join(f"{x['style']}×{x['count']}" for x in body_list[:3]))
        return True, "；".join(parts)

    pool = ThreadPoolExecutor(max_workers=conc)
    futures = {pool.submit(one, b): b for b in targets}
    try:
        for fut in as_completed(futures):
            blogger = futures[fut]
            try:
                ok, msg = fut.result()
            except Exception as e:  # noqa: BLE001
                ok, msg = False, f"{type(e).__name__}: {e}"[:120]
            with _state_lock:
                _state["done"] += 1
                _state["current"] = f"{blogger} → {msg}"
                if ok:
                    _state["ok"] += 1
                elif not _stop.is_set():
                    # 手动停止导致的跳过不算失败
                    _state["failed"] += 1
                    log.warning("风格识别失败 %s: %s", blogger, msg)
            if _stop.is_set():
                for f in futures:
                    f.cancel()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    with _state_lock:
        _state["running"] = False
        _state["finished_at"] = time.time()
        if _stop.is_set() and not _state["error"]:
            _state["error"] = "已手动停止"
    log.info("风格识别结束: 成功 %d，失败 %d，跳过无效图 %d",
             _state["ok"], _state["failed"], _state.get("skipped_blank", 0))


def state() -> dict:
    from . import localmodel
    with _state_lock:
        snap = dict(_state)
    eng = engine()
    snap["configured"] = engine_ready()
    snap["engine"] = eng
    snap["engine_label"] = engine_label()
    snap["llm_configured"] = vision_configured()
    snap["local_model_ready"] = local_model_ready()
    snap["local_model_variant"] = settings.get("local_model_variant") or "quantized"
    snap["labels"] = labels()
    snap["body_labels"] = body_labels()
    snap["model"] = (localmodel.model_name(snap["local_model_variant"]) if eng == "local"
                     else settings.get("vision_model"))
    snap["min_sample"] = MIN_SAMPLE
    snap["keywords"] = {"style": len(keyword_map("style")),
                        "body": len(keyword_map("body"))}
    return snap
