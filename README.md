# DY Sort — 本地短视频分类与 Emby 集成

![License](https://img.shields.io/badge/license-MIT-green) ![Python](https://img.shields.io/badge/python-3.11+-blue) ![Docker](https://img.shields.io/badge/docker-compose-2496ED) ![FastAPI](https://img.shields.io/badge/FastAPI-009688) ![Emby](https://img.shields.io/badge/Emby-4.10.1-52B400)

Docker 化的本地短视频管理工具：媒体库映射总览、浏览预览、一键加入 Emby 媒体库，并支持用图像模型识别博主的穿衣风格与身材标签。配置持久化在 `config/config.yaml`，索引存 SQLite，针对大量视频做了性能优化。

## 技术栈

- **后端**：Python 3.11 + FastAPI + Uvicorn，全异步 IO
- **存储**：SQLite（WAL 模式）索引，文件系统零侵入（只读你的视频目录）
- **前端**：原生 HTML/CSS/JS，暗色主题，无构建步骤
- **部署**：单容器 Docker Compose，配置与索引全部持久化挂载
- **兼容**：针对 **Emby 4.10.1**（4.10.x）开发实测；加入/移出媒体库路径均使用库 Guid 接口

## 亮点速览

| | |
|---|---|
| 🔍 **增量扫描** | 目录 mtime 未变直接跳过，数万级视频重启秒级就绪 |
| 🎬 **在线预览** | 浏览器直接播放，支持 Range 拖动进度条 |
| 📥 **一键入库** | 按博主目录批量加入 Emby，自动触发媒体库扫描 |
| 🗺 **路径映射** | 自动处理容器路径与 Emby 侧路径的差异 |
| ⏱ **定时任务** | 可配置间隔自动重扫，保存即生效无需重启 |
| 🏷 **风格识别** | 本地 CLIP / 云端大模型 / 文字规则三种引擎，风格与身材双维度 |

## 功能

1. **映射总览（首页）**：每个 `DY_` Emby 媒体库对应的本地目录一览，卡片展示、自动折叠长列表，点击博主直达其视频。
2. **视频浏览与预览**：分页网格浏览海报，独立预览页播放（支持拖进度条、同博主上/下一个切换）；预览页**左侧默认显示该博主的其他视频网格**，点一下即可切换，也可一键切回博主列表。
3. **加入 Emby**：勾选视频后点「加入 Emby」，按视频所属**博主目录**（一级目录）批量入库并触发扫描；预览页、识别页的博主详情弹窗也能直接加入。
4. **手动选择媒体库**：点击「加入 Emby」时弹出媒体库列表，由你选择目标库（不在设置里绑定固定的库）。
5. **已存在标注**：已在 Emby 中的博主目录显示绿色「已在 Emby」徽标；支持「同步 Emby 状态」把 Emby 侧手工添加的目录也登记到本地。
6. **只读 DY_ 媒体库**：所有 Emby 操作仅针对 `DY_` 前缀（可在设置中改）的媒体库。
7. **目录绑定**：`/data` 下混有其他媒体时，可在设置中把某个子目录绑定为短视频根目录。
8. **定时重新扫描**：设置页填写间隔（分钟）即开启自动增量扫描，保存后立即生效，无需重启。
9. **实时日志**：独立日志页实时查看扫描/Emby 操作与异常，支持级别过滤。
10. **删除（默认关闭）**：可删除单个视频或整个博主目录（含磁盘文件、索引记录，并可联动移出 Emby 媒体库路径）。受多重边界保护，见下文「删除功能与安全边界」。
11. **媒体库容量自动管理**：以博主目录为单位控制每个 `DY_` 媒体库的视频数，超过上限（默认 2000）时**先填同族还有空位的序号库，都没有空位才新建**（`DY_示例` → `DY_示例2` → `DY_示例3`），**从不拆分同一个目录**。见下文「媒体库容量自动管理」。
12. **博主穿衣风格识别**：独立「图像识别」页，用海报图判断博主的穿衣风格（49 个标签，覆盖袜类、裤装、裙装、上装、外套、鞋类、场景等）**以及身材标签（21 个，覆盖胸部、腿部、腰腹、肩颈、整体身型等）**；**快速识别**每人随机 10 个视频，**全量识别**覆盖全部视频，带进度条、可随时停止，空白/无内容图自动换图重试；点博主弹详情看全部标签，详情里可直接加入 Emby、**弹窗预览该博主全部视频（内嵌播放器，不跳页）**，也能只对他全量识别。见下文「博主穿衣风格识别」。

## 页面

| 页面 | 路径 | 内容 |
|---|---|---|
| 映射 | `/` | Emby 媒体库 → 本地目录总览 |
| 识别 | `/recognize.html` | 图像识别板块：快速/全量识别、进度条、标签筛选、博主详情与单博主识别 |
| 指引 | `/guide.html` | 目录映射三步指引：状态自检、库路径反解校验、一键校准 Emby 侧根 |
| 浏览 | `/browse.html` | 视频网格、按「未映射/已映射目录」筛选（默认未映射）、扫描/同步/勾选后加入 Emby |
| 预览 | `/preview.html?id=` | 播放单视频、左侧默认显示该博主其他视频网格（可切回博主列表）、加入/删除操作 |
| 设置 | `/settings.html` | Emby 连接、定时扫描间隔、目录绑定、识别引擎等持久化配置 |
| 日志 | `/logs.html` | 实时运行日志 |

## 目录结构约定

```
/your/videos/               ← 挂载到容器 /data
├── （其他媒体目录...）      ← 不受影响，可在设置中绑定跳过
├── 短视频/                  ← 可在设置中把该子目录绑定为短视频根目录
│   ├── 博主ID/
│   │   ├── metadata.json   ← 博主级元数据（可选）
│   │   ├── 视频名/
│   │   │   ├── metadata.json ← 视频级元数据（可选，缺省继承博主级）
│   │   │   ├── poster.jpg   ← 海报（可选；也支持 cover.* / thumb.* / 任意图片）
│   │   │   └── 视频名.mp4
│   │   └── ...
│   └── 另一个博主ID/
│       └── ...
└── ...
```

- 不绑定子目录时，直接把博主目录放在 `/data` 根下。
- 兼容旧结构 `/博主ID/视频文件.mp4`。
- **Emby 以博主目录为单位添加**，同一博主下所有视频一起入库。

## 快速开始

镜像已发布到 GitHub Container Registry，无需克隆仓库或本地构建：

1. 准备 `docker-compose.yml`（把 `/path/to/your/videos` 改成你的视频根目录）：

```yaml
services:
  dy-sort:
    image: ghcr.io/zzh407/dy-sort:latest
    container_name: dy-sort
    ports:
      - "8088:8000"
    volumes:
      - /path/to/your/videos:/data   # 你的视频根目录
      - ./config:/app/config         # 配置目录（config.yaml 持久化）
      - ./cache:/app/.cache          # SQLite 索引缓存
    restart: unless-stopped
```

2. 启动（首次会自动生成 `config/config.yaml` 模板）：

```powershell
docker compose pull && docker compose up -d
```

3. 升级到新版本：

```powershell
docker compose pull && docker compose up -d
```

4. 打开 `http://localhost:8088`，首页查看映射总览；在「设置」页填好 Emby 连接信息后保存。
5. 加入 Emby 后若 Emby 里无法播放，说明 Emby 侧路径没对上，按「[映射目录配置指引](#映射目录配置指引)」检查挂载与 `host_video_root`。

> 标签说明：`latest` 跟随主干最新提交，`v0.4.4` 为当前版本 tag；也可以 `docker pull ghcr.io/zzh407/dy-sort:v0.4.4` 精确拉取该版本。
>
> 从源码构建（可选）：克隆本仓库后 `docker compose up -d --build`，compose 中 `build: .` 会替换镜像方式。

## 配置文件 config/config.yaml

首次运行自动生成带注释的模板，三种修改方式等价（改 yaml 后需重启容器）：

- 直接编辑 `config/config.yaml`（便于手工调整和版本管理）
- 网页「设置」页保存（写回 yaml 且保留注释）
- 环境变量提供**默认值**（yaml 中对应键留空时生效，如 `EMBY_URL`）

```yaml
# Emby 服务器地址与 API Key（Emby 控制台 -> 高级 -> API 密钥）
emby_url: "http://192.168.1.10:8096"
emby_api_key: "xxxxx"

# 只操作名称以该前缀开头的媒体库
emby_library_prefix: "DY_"

# 媒体库容量自动管理：加入 Emby 时按博主目录整体分流，
# 超过上限的库不再追加，优先填入同族有空位的序号库，没有才新建
emby_auto_library: true
emby_library_max_videos: 2000

# 绑定的短视频子目录：/data 下可能混有其他媒体
media_subdir: ""

# Emby 侧视频根路径：Emby 看到的路径与容器 /data 不一致时填写
host_video_root: ""

# 列表每页数量
page_size: 60

# 定时重新扫描间隔（分钟），0 = 关闭
scan_interval: 0
```

| 键 | 默认 | 环境变量默认值 |
|---|---|---|
| `emby_url` / `emby_api_key` | 空 | `EMBY_URL` / `EMBY_API_KEY` |
| `emby_library_prefix` | `DY_` | `EMBY_LIBRARY_PREFIX` |
| `emby_auto_library` | `true` | `EMBY_AUTO_LIBRARY` |
| `emby_library_max_videos` | `2000` | `EMBY_LIBRARY_MAX_VIDEOS` |
| `style_labels` | 49 个风格标签（可在设置页增删） | `STYLE_LABELS` |
| `body_labels` | 21 个身材标签（可在设置页增删） | `BODY_LABELS` |
| `recognition_engine` | `local`（本地模型，默认） | `RECOGNITION_ENGINE` |
| `local_model_variant` / `local_model_dir` | `quantized` / `/app/.cache/models` | `LOCAL_MODEL_VARIANT` / `LOCAL_MODEL_DIR` |
| `local_model_mirror` / `local_model_auto_download` | `https://hf-mirror.com` / `true` | `LOCAL_MODEL_MIRROR` / `LOCAL_MODEL_AUTO_DOWNLOAD` |
| `vision_base_url` / `vision_api_key` / `vision_model` | `https://open.bigmodel.cn/api/paas/v4` / 空 / `glm-4v-flash`（智谱免费） | `VISION_BASE_URL` / `VISION_API_KEY` / `VISION_MODEL` |
| `vision_sample_videos` / `vision_batch_images` | `10` / `1` | `VISION_SAMPLE_VIDEOS` / `VISION_BATCH_IMAGES` |
| `vision_concurrency` | `2` | `VISION_CONCURRENCY` |
| `media_subdir` | 空 | `MEDIA_SUBDIR` |
| `host_video_root` | 空 | `HOST_VIDEO_ROOT` |
| `page_size` | `60` | `PAGE_SIZE` |
| `scan_interval` | `0`（关闭） | `SCAN_INTERVAL` |
| `enable_delete` | `false`（关闭） | `ENABLE_DELETE` |

## 性能设计（大量视频）

- **SQLite 索引**：扫描结果、Emby 映射全部入库（WAL 模式），列表页是纯 SQL 分页查询，不遍历文件系统。
- **增量扫描**：目录 mtime 未变的博主只刷新时间戳、不遍历内部（重启后秒级完成）；变化/新增的才完整扫描；自动清理已删除目录的记录。
- **IO 优化**：每个目录单次 `iterdir`，条目复用于海报/元数据/子目录；批量事务提交（每 50 个博主一次 commit）。
- **后台任务**：扫描在后台线程跑，前端轮询进度条，不阻塞页面。
- **分页**：默认每页 60 条，筛选（博主/状态/搜索词）全部下推到 SQL。

## 映射目录配置指引

> 不想看文字？打开网页 **「指引」** 页（`/guide.html`）：它把下面三步做成带状态灯的卡片，
> 并用现有 Emby 库路径反推出正确的「Emby 侧根」，点 **一键校准** 直接写回配置。
> 三步都打勾即说明映射正确。

「映射」要解决一件事：本工具把**博主目录**路径交给 Emby 时，Emby 必须能按同一路径访问到同一份文件。涉及三个概念：

| 概念 | 在哪配置 | 含义 |
|---|---|---|
| 容器路径 `/data` | compose 的 `volumes` | 本工具看到的视频根目录 |
| 绑定子目录 `media_subdir` | 设置页「目录绑定」 | `/data` 下混有其他媒体时，把短视频所在子目录绑定为根；留空 = 直接用 `/data` 根 |
| Emby 侧根 `host_video_root` | 设置页「Emby 侧视频根路径」 | `/data` 对应的、**Emby 看到的那条路径**；与 `/data` 一致时留空 |

加入 Emby 的博主目录按此公式生成：

```
host_video_root（留空则为 /data）[ / 绑定子目录] / 博主ID
```

| 配置组合 | 交给 Emby 的路径 |
|---|---|
| 未绑定子目录、未填 Emby 侧根 | `/data/博主ID` |
| 绑定 `短视频`、未填 Emby 侧根 | `/data/短视频/博主ID` |
| 绑定 `短视频` + Emby 侧根 `D:\Videos` | `D:\Videos\短视频\博主ID` |

### 配置步骤

1. **挂载**：把视频根目录挂到容器 `/data`（compose `volumes`），视频按上文「目录结构约定」摆放（博主ID 为一级目录）。
2. **绑定子目录（可选）**：设置页「目录绑定」浏览 `/data` 子目录并绑定；换绑会清空索引并自动重扫。
3. **填 Emby 侧根（可选）**：仅当 Emby 看到的路径与 `/data` 不一致时填写，典型场景见下表。
4. **扫描并入库**：保存后点浏览页「扫描」（或等定时扫描），在首页映射总览确认无误后勾选视频「加入 Emby」。

### 场景示例：NAS 上两个容器

视频放在 NAS 的共享目录里，dy-sort 与 Emby 各自以容器运行，并挂载同一份父目录：

```text
磁盘目录（示意）
/mnt/media/videos/
├── 博主A/
│   ├── metadata.json        （可选）
│   └── 视频名/
│       ├── poster.jpg       （可选）
│       └── 视频名.mp4
└── 博主B/…
```

dy-sort 容器把视频目录挂载为 `/data`：

```yaml
services:
  dy-sort:
    image: ghcr.io/zzh407/dy-sort:latest
    ports:
      - "8088:8000"
    volumes:
      - /mnt/media/videos:/data   # 短视频根目录
      - ./config:/app/config
      - ./cache:/app/.cache
```

Emby 容器把父目录挂载为 `/media`（关键：两个容器访问同一份文件）：

```yaml
services:
  emby:
    image: emby/embyserver
    volumes:
      - /mnt/media:/media
```

设置页对应两项（`config/config.yaml` 等价写法）：

```yaml
media_subdir: ""              # 博主目录直接放在 /data 根，无需绑定子目录
host_video_root: "/media/videos"   # /data 对应的 Emby 侧路径
```

完整路径流：

```text
磁盘      /mnt/media/videos/博主A
  → dy-sort 容器   /data/博主A            （挂载 /mnt/media/videos → /data）
  → 加入 Emby 换算 /media/videos/博主A     （host_video_root 替换 /data）
  → Emby 容器      /media/videos/博主A     （挂载 /mnt/media → /media）✓ 同一份文件
```

### 典型场景

| 场景 | 挂载关系 | `host_video_root` 填写 |
|---|---|---|
| Emby 与本工具路径一致 | Emby 也挂载 `/data`，或同宿主机同路径 | 留空 |
| Emby 在 Windows 宿主机 | `D:\Videos` 挂载为 `/data` | `D:\Videos` |
| Emby 在另一个容器 | 同一宿主目录分别挂进两个容器，Emby 内路径为 `/media/videos` | `/media/videos` |
| 群晖等 NAS 原生 Emby | 共享文件夹 `/volume1/videos` 挂载为 `/data` | `/volume1/videos` |

### 验证与排错

- **首页映射总览**：会把 Emby 库路径反向解析回本地路径展示；解析错位 = `host_video_root` 与实际不符。
- **「同步 Emby 状态」**：把 Emby 侧手工添加的目录也登记到本地映射。
- **`GET /api/emby/map`**：直接查看本地路径 ↔ Emby 路径对应表。
- 加入成功但 Emby 里「媒体不存在」/无法播放 → Emby 按该路径访问不到文件，检查 Emby 侧挂载映射；Linux 路径**大小写敏感**，须完全一致。对应关系保存在 SQLite `emby_map` 表，与映射总览同源。

### 一键校准的判断依据

「指引」页会读取所有 `DY_` 库的路径（例如 `/media/videos/博主A`），去掉最后一段目录名后统计出现最多的前缀，
作为「Emby 侧根」的建议值；再把每条路径按当前配置反解回本地目录，逐条标 ✅/⚠️。
它只校验**字符串对应关系**，无法验证 Emby 容器里是否真能读到文件——校准完请到 Emby 播放一个视频确认。

## 博主穿衣风格识别

预览页左侧默认列出**当前博主的其他视频**（海报网格，点一下直接切过去播），右上角「博主列表」按钮可切回全部博主（可搜索、按标签筛选）；博主列表里每个博主 ID 后面跟着两组标签：穿衣风格 `[标签A×6]` 和身材 `[标签B×7]`（身材标签是暖色圆角块，与风格区分）。标签点一下就能改。

**点博主名 → 弹出他全部视频的网格**（海报 + 名称 + 体积，48 个一页，可翻页），点任意一个直接在当前页播放；播放器下方的「上一个 / 下一个」仍在该博主内切换。

### 默认标签集

| 维度 | 默认标签（可在设置页增删） |
|---|---|
| 穿衣风格 | 49 个，按袜类、裤装、裙装、上装、外套、鞋类、场景等大类组织 |
| 身材 | 21 个，按胸部、腿部、腰腹、肩颈、整体身型等大类组织 |

标签越全，模型越容易「就近归类」；如果你觉得某些标签永远不会出现，删掉它们能让判断更稳。

### 两个维度

| 维度 | 标签来源 | 说明 |
|---|---|---|
| 穿衣风格 | `style_labels` | 每张图**必须**选一个（都不符合给「其他」） |
| 身材 | `body_labels` | 大模型引擎每张图可选 **0~2 个**（确实看得出来才标），例如 `["标签A","标签B"]`；本地 CLIP 引擎单图只报最像的一个大类 |

两组标签**分开存储、分开计数、按各自命中次数排序**：`blogger_style.labels` 存风格、`blogger_style.body_labels` 存身材，UI 用不同样式区分。模型只描述画面可观察的特征，判断不出来就留空。

### 标签从哪来（优先级从低到高）

| 来源 | 怎么产生 | 说明 |
|---|---|---|
| `name` | 从博主名里的文字猜 | 置信度 0.5，只是个兜底 |
| `library` | 从 Emby 库名猜（库名本身就是标签名时） | 置信度 0.9；启动时自动跑，几乎零成本 |
| `local` | 本地文字规则（不读图，按库名/博主名/视频名匹配） | 引擎选 `rules` 时使用 |
| `local-model` | 内置 CLIP 模型逐张看海报 | 默认引擎；结果写库并缓存 |
| `vision` | 云端视觉大模型看海报（默认智谱 GLM-4V-Flash，填 API Key 即用） | 只需 `vision_api_key`，地址/模型有默认值 |
| `manual` | 手动点选 | 最高优先级，批量识别永远不会覆盖它 |

同一博主多条来源时按上表优先级取最高；**库名/博主名预标注只补空位，不会覆盖识别结果，也不会清空身材标签**。

### 识别引擎

设置页「识别引擎」三选一（`recognition_engine`），默认 **本地模型**：

| 引擎 | 行为 | 需要的配置 |
|---|---|---|
| `local`（默认） | **内置 CLIP 本地模型**：首次识别自动下载 ONNX（quantized 约 155MB）到 `local_model_dir`，之后在容器内 CPU 推理，不依赖 Ollama 等外部服务、不联网 | `local_model_variant`（quantized/fp32）、`local_model_dir`、`local_model_mirror`、`local_model_auto_download` |
| `llm` | 云端大模型（OpenAI 兼容），按图逐张判断；**默认智谱 GLM-4V-Flash（免费），只需填 API Key** | `vision_api_key`（地址/模型已有默认值） |
| `rules` | 不读图片，只用库名 / 博主名 / 视频名打标，零成本 | 无 |

本地模型说明：

- 模型是 CLIP ViT-B/32（Xenova 的 ONNX 导出版），quantized 约 155MB、fp32 约 600MB；
- 默认从 `https://hf-mirror.com` 下载，可改成 `https://huggingface.co`（需能直连，或走代理）；
- 文件落在 cache 卷（默认 `/app/.cache/models/quantized/`），容器重建/升级不会重新下载；
- 下载进度会显示在识别页进度条上（`下载本地模型 xx/155 MB`），首次识别会多花十几秒；
- 文本侧只编码一次（每个标签 2 条英文提示词），编码结果缓存成 `.npz`，之后每张图只跑视觉塔；
- 实测速度：CPU 单张 **13–20ms**；1000 张约 20 秒。

大模型侧只要求 `POST {地址}/chat/completions` 且消息里能带 `image_url`（data URI 传图）。默认接智谱，**只需要在设置里填 API Key**：

| 字段 | 默认/例子 |
|---|---|
| 地址 | 默认 `https://open.bigmodel.cn/api/paas/v4`（智谱）；也可填 `https://api.openai.com/v1`、`https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 模型名 | 默认 `glm-4v-flash`（智谱免费图像理解）；也可填 `glm-5.3-flash`、`gpt-4o-mini`、`qwen-vl-max` |
| API Key | 在 [智谱开放平台](https://bigmodel.cn/usercenter/proj-mgmt/apikeys) 申请 |

> 配置项**全部持久化在 `config/config.yaml`**：启动时会把缺失的键（含空值）补齐写回文件，所以文件本身就是完整清单。

### 一次识别是怎么跑的

**两种模式**（「识别」页顶部两个按钮）：

| 模式 | 范围 | 每张图请求 | 适合 |
|---|---|---|---|
| 快速识别 | 尚未标注的博主，每人**随机 10 个视频** | 1 张 1 次 | 第一次铺开，便宜快速 |
| 全量识别 | 所有博主的**全部视频**（可只对某个博主） | 1 张 1 次 | 追求准确，费用与耗时按视频数线性增长 |

流程：

1. 按模式决定每人要判多少张：快速 = `vision_sample_videos`（默认 10，至少 10）；全量 = 该博主的全部视频；
2. 从该博主的全部海报里**随机**取图；纯色/纯黑/纯白/没细节的图先在本地用 Pillow 统计灰度方差筛掉（`跳过无内容图` 计数 +1），**换下一张补位**；
3. 模型如果仍判定「无内容」（提示词里明确要求返回 `无内容`），同样换一张重新识别，保证有效样本数尽量凑满；
4. 每张图返回 `{"style":"标签A","body":["标签B"]}`，标签先规整到两个标签集合里，认不出来的丢弃；
5. 两个维度各自统计命中次数并降序，写入 `blogger_style`；
6. 进度条轮询 `/api/styles/state`：博主 x/y、图片 x/y、成功/失败、跳过无内容数、当前博主；随时可点「停止」。

失败不影响其他博主；人工标注过的博主（`manual`）任何模式都不会覆盖。

### 博主详情窗

「识别」页点任意博主 → 弹窗展示**全部标签**（风格与身材分开、带命中张数和占比条）、来源、备注，以及四个动作：

- **加入 Emby**：选择媒体库后把该博主目录整体入库；
- **预览视频**：弹窗内嵌播放器 + 该博主视频网格，点任意视频直接播放，关闭后回到详情，不离开识别页；
- **快速识别该博主**：只对他抽 10 个视频；
- **全量识别该博主**：只把他所有视频过一遍（长任务，可随时停）。

### 成本与隐私

- 快速模式：每个博主约 10 次请求（博主数 × 10）；全量模式 ≈ 全库视频总数。
- 想省钱把「每次请求图片数」调到 2~5，请求数降到 1/2~1/5（模型可能少判几张）。
- **海报图会发送到你配置的那个 API**；不配置模型时功能仍可用（库名预标注 + 人工标注，图片不出本机）。

### 识别不准怎么办

- 改「穿衣风格标签 / 身材标签」列表，让它贴合你的素材；保存后立即生效，重新识别即可。
- 单博主点标签直接改（风格与身材都可多选），人工结果优先级最高。
- 只重跑某几个：`POST /api/styles/run {"bloggers":["A","B"],"mode":"full","force":true}`。

### 本地模型实测准确率

人工标注样本上的命中率（CLIP ViT-B/32 quantized，CPU 单张 13–20ms）：

| 维度 | 标注量 | 细标签 | 大类 |
|---|---|---|---|
| 穿衣风格 | 18 张 | top1 5/18、top3 12/18 | top1 12/18、top3 17/18 |
| 身材 | 36 张 | — | 单标签 top1 22/36（61%） |

身材维度单图只报最像的一个大类（阈值 0.25），精确率约 0.73、召回率 0.33（21 标签）；细标签/多标签/两档阈值的完整对比和 36 张标注明细见 [docs/body-eval.md](docs/body-eval.md)。本地 CLIP 对部分细标签接近噪声，这类标签建议交给大模型引擎。

结论：风格大类（裙装/裤装/袜类…）可用；身材只能当弱提示——胸部、身高、肩颈在本地 CLIP 上误报偏多，要准请切「大模型」引擎。

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/videos` | 分页列表（page/page_size/q/blogger/in_emby） |
| GET | `/api/video/{id}` | 单视频完整信息（预览页） |
| GET | `/api/video/{id}/poster` `/stream` | 海报/视频流（支持 Range） |
| POST | `/api/scan` + GET `/api/scan/state` | 后台扫描与进度（`scheduler` 字段含定时扫描配置与下次触发时间） |
| GET | `/api/emby/libraries` | 列出 DY_ 媒体库（含每个库的视频数与容量上限） |
| GET | `/api/emby/overview` | 媒体库 → 本地目录映射（首页） |
| POST | `/api/emby/sync` | 同步 Emby 实际路径到本地映射 |
| POST | `/api/emby/add` | 加入博主目录 `{"bloggers":[...],"library_name":"DY_xxx"}` 空 bloggers 则全部未加入；开启自动管理时按容量分流 |
| POST | `/api/emby/move` | 目录整体换库 `{"blogger":"xx","to_library":"DY_x"}`，或 `{"blogger":"xx","create_new":true}` 新建序号库 |
| GET | `/api/emby/rebalance/preview` | 整理方案预览：超限库 → 同族有空位库/新建库的迁移计划（不执行）；`?libraries=A,B` 只算指定库，省略 = 全部 |
| POST | `/api/emby/rebalance` | 执行整理 `{"confirm":true,"libraries":["A"]}`，`libraries` 省略 = 一键整理全部超限库 |
| POST | `/api/emby/remove` | 移出博主目录 `{"blogger":"xx","confirm":true}`，仅删库路径条目不动本地文件 |
| GET | `/api/emby/map` | 查看路径对应关系 |
| GET | `/api/bloggers` | 博主列表（视频数、已入库、风格标签），`q`/`style` 过滤 |
| GET | `/api/styles/state` | 识别任务进度（模式/博主/图片/跳过无内容数）与是否已配置视觉模型 |
| POST | `/api/styles/run` | 启动识别 `{"mode":"quick"|"full","bloggers":[],"force":true,"sample":10,"batch":1}`；quick=每人 10 个，full=全部视频 |
| POST | `/api/styles/stop` | 停止当前识别任务（当前批次跑完即结束） |
| POST | `/api/styles/seed` | 按 Emby 库名 + 博主名做零成本预标注 |
| POST | `/api/styles/set` | 人工修正 `{"blogger":"xx","styles":["标签A","标签B"],"bodies":["标签C"]}`，都为空 = 清除；也兼容 `{"style":"标签A"}` |
| GET | `/api/mapping/diagnose` | 映射自检：三步状态 + 库路径反解 + Emby 侧根建议（只读） |
| POST | `/api/mapping/autofix` | 一键校准 Emby 侧根 `{"apply":true}`，写回 `config.yaml`；`apply=false` 只回建议 |
| GET | `/api/dirs` | 浏览 /data 子目录（绑定短视频根目录） |
| POST | `/api/bind` | 绑定短视频根目录 `{"media_subdir":"..."}`，换绑重建索引 |
| GET/POST | `/api/settings` | 读取/保存设置（写回 config/config.yaml） |
| GET | `/api/logs?after=N` | 增量拉取运行日志（前端轮询） |

## 媒体库容量自动管理

Emby 媒体库太大时（单个库上万条视频），扫描、浏览和客户端加载都会变慢。本工具把「每个 `DY_` 库最多多少视频」做成可配置项，并在**加入 Emby** 时自动分流。

| 规则 | 说明 |
|---|---|
| 计量口径 | Emby 侧每个 DY_ 库的 `Video` + `Movie` 条目数（`/Items?Recursive=true&Limit=0`），数百毫秒级 |
| 权重换算 | 本地索引按「视频目录」计数，Emby 按文件计数（一个目录可能有多集），实测 Emby ≈ 本地的 1.0~1.25 倍；规划时按实测比例放大待加入目录的权重，避免少算导致悄悄超限 |
| 上限 | `emby_library_max_videos`，默认 `2000`；严格大于才算超限 |
| 目录不可拆 | 博主目录是分流的最小单位，一个目录里的视频永远在同一个库 |
| 新建命名 | **同族已有空位的序号库优先**：`DY_示例` 满了先看 `DY_示例2`/`3`… 有没有空位，能放下就迁过去，都放不下才新建下一个序号；序号只增不复用。建库前会重新核对 Emby 现有库名，撞名就自动 +1 再试，最多 8 次 |
| 家族识别 | 只有「去掉尾部数字后的名字本身是已存在的库」才算同一家族：`DY_示例3` 属于 `DY_示例`，`DY_2024` 不会被误判成 `DY_2` 家族 |
| 装填顺序 | 待加入目录按视频数降序装箱：先填目标库剩余空间，再填同族其它还有空位的库（序号升序），最后才续建新库 |
| 单目录超限 | 某目录自身就超过上限时独占一个新库，并在提示里说明（仍不拆分目录） |
| 关闭 | 设置页取消「自动管理媒体库容量」，或 `config.yaml` 设 `emby_auto_library: false`，行为回到全部塞进所选库 |

映射页额外提供两个动作：

- **移动**：任意目录可整体移到另一个 DY_ 库，或选择「新建同名序号库」承接。实现顺序是**先加入目标库、再从原库移除**，中途失败不会出现两边都丢。
- **整理**：库头出现「超出上限」徽标时点「整理」，只整理**这一个库**；先看预览（哪个目录去哪个库：**同族有空位的库优先，都没有才新建**、迁移多少个视频），确认后执行。按目录体积**从大到小**挑，用最少的迁移次数把库降到上限以内；单个目录自身就超限的会整体迁到新库并告警（那个库仍会超限，因为目录不可拆）。
- **一键整理**：概览标题栏右侧的按钮，一次处理**所有**超限库，同样先出预览再确认；每个库各自按上限切分，互不影响。执行时会重新计算方案（不是沿用预览数据），所以期间库里内容有变化也不会错位。
- **名称隐藏**：标题栏的眼睛按钮可把媒体库名、目录名和目录路径末段换成 `••••`，状态记在浏览器本地，刷新后保持；隐藏时点击行不再跳转，避免误操作。视频数、目录数、超限状态照常显示。

```text
DY_示例A     2500 / 2000   超限 → 整理时迁出放不下的目录
DY_示例A2    1500 / 2000   有空位 → 优先承接迁出的目录
DY_示例A3     新建            ← 2 也装满后才新建，按 2000 一档继续细分
```

> 迁移只改 Emby 媒体库的**路径归属**，不动磁盘文件，Emby 会重新扫描迁入的目录。若某些条目元数据是手工修正过的，换库后可能需要重新刮削，执行前先看预览。

## 删除功能与安全边界

删除功能**默认关闭**，需在「设置」页（或 `config/config.yaml`）把 `enable_delete` 设为 `true`，也可用环境变量 `ENABLE_DELETE=true` 提供默认值。开启后仍受以下边界约束：

| 边界 | 说明 |
|---|---|
| 功能开关 | `enable_delete=false` 时所有删除接口返回 403 |
| 挂载范围 | 目标必须位于容器挂载点 `/data` 之内，且永远不允许删除 `/data` 根 |
| 深度边界 | 博主目录 = 短视频根目录的一级子目录；视频目录 = 博主目录下一级。越层路径一律拒绝，不存在「向上删除」 |
| 保留名黑名单 | `@eaDir`、`lost+found`、`#recycle`、`.Trash-*`、`metadata.json` 等名称不可删除；`.` 开头目录与扫描器保持一致被忽略 |
| 二次确认 | 单视频：勾选确认框；整个博主：必须**手动输入博主名**解锁删除按钮 |

两种删除粒度严格区分：

- **删除单个视频**：`DELETE /api/video/{id}` — 删除视频目录（`博主/视频名/`，含海报与元数据）+ 索引记录；旧结构（文件直接在博主目录下）只删该文件。
- **删除整个博主**：`DELETE /api/blogger/{name}` — 删除整个博主目录树 + 全部视频/博主/`emby_map` 记录，默认同时把该目录从 Emby 媒体库移除（`remove_from_emby=false` 可保留）。删除前可用 `GET /api/delete/blogger/{name}/preview` 查看将删除的视频数与体积。

删除均先删索引记录再删磁盘：即使磁盘删除失败留下残留，下次扫描也不会复现幽灵记录。批量删除（`POST /api/delete/bloggers`，1~50 个）逐个执行并在前端逐个弹窗确认。

## 许可证

[MIT](LICENSE) © 2026 zzh407

## Star History

如果这个项目对你有帮助，欢迎点个 Star ⭐
