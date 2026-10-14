# DY Sort — 本地短视频分类与 Emby 集成

![License](https://img.shields.io/badge/license-MIT-green) ![Python](https://img.shields.io/badge/python-3.11+-blue) ![Docker](https://img.shields.io/badge/docker-compose-2496ED) ![FastAPI](https://img.shields.io/badge/FastAPI-009688)

Docker 化的本地短视频管理工具：媒体库映射总览、浏览预览、一键加入 Emby 媒体库。配置持久化在 `config/config.yaml`，索引存 SQLite，针对大量视频做了性能优化。

## 技术栈

- **后端**：Python 3.11 + FastAPI + Uvicorn，全异步 IO
- **存储**：SQLite（WAL 模式）索引，文件系统零侵入（只读你的视频目录）
- **前端**：原生 HTML/CSS/JS，暗色主题，无构建步骤
- **部署**：单容器 Docker Compose，配置与索引全部持久化挂载

## 亮点速览

| | |
|---|---|
| 🔍 **增量扫描** | 目录 mtime 未变直接跳过，7 万+ 视频重启秒级就绪 |
| 🎬 **在线预览** | 浏览器直接播放，支持 Range 拖动进度条 |
| 📥 **一键入库** | 按博主目录批量加入 Emby，自动触发媒体库扫描 |
| 🗺 **路径映射** | 自动处理容器路径与 Emby 侧路径的差异 |
| ⏱ **定时任务** | 可配置间隔自动重扫，保存即生效无需重启 |

## 功能

1. **映射总览（首页）**：每个 `DY_` Emby 媒体库对应的本地目录一览，卡片展示、自动折叠长列表，点击博主直达其视频。
2. **视频浏览与预览**：分页网格浏览海报，独立预览页播放（支持拖进度条、同博主上/下一个切换）。
3. **加入 Emby**：勾选视频后点「加入 Emby」，按视频所属**博主目录**（一级目录）批量入库并触发扫描；预览页的「加入 Emby」加入当前视频所属博主目录。
4. **手动选择媒体库**：点击「加入 Emby」时弹出媒体库列表，由你选择目标库（不再在设置里绑定固定的库）。
5. **已存在标注**：已在 Emby 中的博主目录显示绿色「已在 Emby」徽标；支持「同步 Emby 状态」把 Emby 侧手工添加的目录也登记到本地。
6. **只读 DY_ 媒体库**：所有 Emby 操作仅针对 `DY_` 前缀（可在设置中改）的媒体库。
7. **目录绑定**：`/data` 下混有其他媒体时，可在设置中把某个子目录绑定为短视频根目录。
8. **定时重新扫描**：设置页填写间隔（分钟）即开启自动增量扫描，保存后立即生效，无需重启。
9. **实时日志**：独立日志页实时查看扫描/Emby 操作与异常，支持级别过滤。

## 页面

| 页面 | 路径 | 内容 |
|---|---|---|
| 映射 | `/` | Emby 媒体库 → 本地目录总览 |
| 浏览 | `/browse.html` | 视频网格、按「未映射/已映射目录」筛选（默认未映射）、扫描/同步/勾选后加入 Emby |
| 预览 | `/preview.html?id=` | 单视频播放与加入操作 |
| 设置 | `/settings.html` | Emby 连接、定时扫描间隔、目录绑定等持久化配置 |
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

1. 编辑 `docker-compose.yml`：把 `/path/to/your/videos` 改成你的视频根目录，并挂载 `./config` 目录：

```yaml
volumes:
  - /your/videos:/data       # 你的视频根目录
  - ./config:/app/config     # 配置目录（config.yaml 持久化）
  - ./cache:/app/.cache      # SQLite 索引缓存
```

2. 启动（首次会自动生成 `config/config.yaml` 模板）：

```powershell
docker compose up -d --build
```

3. 打开 `http://localhost:8088`，首页查看映射总览；在「设置」页填好 Emby 连接信息后保存。

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
| `media_subdir` | 空 | `MEDIA_SUBDIR` |
| `host_video_root` | 空 | `HOST_VIDEO_ROOT` |
| `page_size` | `60` | `PAGE_SIZE` |
| `scan_interval` | `0`（关闭） | `SCAN_INTERVAL` |

## 性能设计（大量视频）

- **SQLite 索引**：扫描结果、Emby 映射全部入库（WAL 模式），列表页是纯 SQL 分页查询，不遍历文件系统。
- **增量扫描**：目录 mtime 未变的博主只刷新时间戳、不遍历内部（重启后秒级完成）；变化/新增的才完整扫描；自动清理已删除目录的记录。
- **IO 优化**：每个目录单次 `iterdir`，条目复用于海报/元数据/子目录；批量事务提交（每 50 个博主一次 commit）。
- **后台任务**：扫描在后台线程跑，前端轮询进度条，不阻塞页面。
- **分页**：默认每页 60 条，筛选（博主/状态/关键词）全部下推到 SQL。

## Emby 路径映射（重要）

加入媒体库的是**博主目录**路径，规则：

- `host_video_root` 为空时 → 容器内实际路径（如 `/data/短视频/博主ID`，含绑定子目录）
- 填了（如 `D:\Videos`，即 `/data` 对应的 Emby 侧路径）→ `D:\Videos\短视频\博主ID`（自动拼接绑定的子目录）

**Emby 必须能以该路径访问到同一份文件**：Emby 在宿主机/另一容器时务必配置该映射。对应关系保存在 SQLite `emby_map` 表，可用「同步 Emby 状态」与 Emby 实际库对齐；首页映射总览也会把 Emby 路径反向解析回容器内本地路径展示。

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/videos` | 分页列表（page/page_size/q/blogger/in_emby） |
| GET | `/api/video/{id}` | 单视频完整信息（预览页） |
| GET | `/api/video/{id}/poster` `/stream` | 海报/视频流（支持 Range） |
| POST | `/api/scan` + GET `/api/scan/state` | 后台扫描与进度（`scheduler` 字段含定时扫描配置与下次触发时间） |
| GET | `/api/emby/libraries` | 列出 DY_ 媒体库 |
| GET | `/api/emby/overview` | 媒体库 → 本地目录映射（首页） |
| POST | `/api/emby/sync` | 同步 Emby 实际路径到本地映射 |
| POST | `/api/emby/add` | 加入博主目录 `{"bloggers":[...],"library_name":"DY_xxx"}` 空 bloggers 则全部未加入 |
| GET | `/api/emby/map` | 查看路径对应关系 |
| GET | `/api/dirs` | 浏览 /data 子目录（绑定短视频根目录） |
| POST | `/api/bind` | 绑定短视频根目录 `{"media_subdir":"..."}`，换绑重建索引 |
| GET/POST | `/api/settings` | 读取/保存设置（写回 config/config.yaml） |
| GET | `/api/logs?after=N` | 增量拉取运行日志（前端轮询） |

## 许可证

[MIT](LICENSE) © 2026 zzh407

## Star History

如果这个项目对你有帮助，欢迎点个 Star ⭐
