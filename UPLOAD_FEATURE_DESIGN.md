# 作业上传与识别入库 — 设计方案 v1

> 状态：**设计稿，未实现**（按约定先规划后编码）
> 入口：侧边栏左下角新增「📤 上传」，按孩子（Diya / Jemma）分别上传作业、报告等文件
> 关联：本文档 §3 OCR 方案同时是「任务 1 OCR 方案确认」的落地建议

---

## 0. 目标与边界

**做**：上传作业/报告（截图 PNG/JPG、PDF）→ OCR 识别 → 文本清洗 → 结构化抽取 → 入库 → 前端可浏览、可跳转错题本。
**不做（v1）**：Word/Excel 解析、在线拍照（摄像头 API）、多人多角色权限（家庭场景保持单密码）、自动判分。

---

## 1. 页面与交互流程

### 1.1 入口与页面结构

- 侧边栏左下角（`sidebar-bottom`，LLM 设置上方）新增 `📤 Upload` 链接，样式与现有 Planning Docs 链接一致。
- `app.html` 新增第九个 tab-panel `#tab-upload`（复用现有 `switchTab()` 机制，无需新页面）；`jemma.html` 同步加（默认锁定 `child=jemma`）。

### 1.2 页面布局（单页三区）

```
┌─ Upload for: [Diya ▾]（顶部选择孩子，jemma.html 隐藏此选择器）─────────┐
│                                                                      │
│ ① 拖拽/点选上传区                                                    │
│    支持 PNG/JPG/WEBP 单张 ≤10MB；PDF ≤30MB 且 ≤20 页                 │
│    多选批量上传，逐文件独立排队                                       │
│                                                                      │
│ ② 上传队列（每文件一行）                                             │
│    [缩略图] filename.pdf  ▸ 页数 12  ▸ 状态徽标  ▸ 进度条  [✕移除]   │
│    状态徽标：排队中 → 上传中 xx% → 识别中 → 完成 / 失败(原因+重试)    │
│                                                                      │
│ ③ 识别结果预览（点击队列项展开）                                     │
│    左：原图/PDF页预览          右：抽取字段表单（可修改后保存）       │
│    字段：科目/类型/日期/标题/分数 + 全文（只读）+ [存入错题本] 按钮  │
└──────────────────────────────────────────────────────────────────────┘
```

### 1.3 交互细节

- **类型自动判断**：文件名/内容含 `report|assessment|grade` → `report`；默认 `homework`。下拉可改。
- **保存动作**：识别结果默认**自动入库**（省一步），字段表单用于**事后修正**（PUT 覆盖）；「存入错题本」是可选的二次动作（把抽取的错题落 `errors_<child>.json`）。
- **空态/引导**：队列空时显示 3 步引导（选孩子 → 拖文件 → 自动识别）。

---

## 2. 文件存储方案

### 2.1 目录结构（ECS 本地盘，不入 Git）

```
$DATA_DIR/
├── uploads/<child>/2026-10/<upload_id>/     # 每次上传一个独立目录
│   ├── original.pdf                          # 原始文件（原名存元数据，磁盘用 id 命名防路径注入）
│   ├── page-01.png ... page-NN.png           # PDF 渲染页 / 图片原图（统一转 PNG 备用）
│   └── meta.json                             # 本上传的元数据+状态机（见 §5）
└── uploads_index.json                        # 全量索引（children→条目摘要），读接口的列表来源
```

### 2.2 关键规则

| 项 | 规则 |
|---|---|
| upload_id | `yyyymmdd-hhmmss-<uuid4 前 6 位>`，全局唯一，路径安全 |
| 原文件名 | 只存 `meta.json.original_name`，展示时转义；磁盘名一律用 upload_id |
| 大小限制 | 图片 ≤10MB、PDF ≤30MB/≤20 页（nginx `client_max_body_size 35m` 需同步调） |
| 清理策略 | 识别成功且已入库 → 保留原图与页图（错题溯源用）；**失败重试 3 次后**保留原文件、删页图；磁盘水位 >80% 时 cron 清理 90 天前失败件 |
| 备份 | `backup.sh` 现有逻辑把 `data/` 纳入即可（uploads 体积大，**不进**每日备份，只进每周归档） |

---

## 3. OCR 调用与文本清洗策略（含任务 1 结论）

### 3.1 现状结论（2026-10-14 实测）

| 项 | 现状 |
|---|---|
| 后端实现 | `api_server.py` `/api/ocr` + `_call_vision_ocr()`：OpenRouter 4 模型级联（ernie-4.5-vl → seed-2.0-lite → llama-4-maverick → qianfan-ocr-fast） |
| **线上状态** | **已全挂**。实测一张 190KB 化学作业图，35.6s 后返回空文本，最终错误 `baidu/qianfan-ocr-fast: 404`（模型下线，前 3 个模型也接连失败） |
| 前端接入 | **0 处调用**（app.html 无任何 `/api/ocr` 引用）——OCR 此前只被旧服务器脚本（`/root/.hermes/image_cache`）使用 |
| 历史质量 | `diya_chem_ocr_results.json`（20 页 GCSE 化学卷，ernie-4.5-vl 识别）：单页 259–1124 字符，**8/20 页输出带"The image is a transcription of..."等解释性前言污染**，需清洗 |
| 主 LLM | 阿里百炼 `qwen3.8-flash`（OpenAI 兼容模式，`token-plan.maas.qianwenaiapi.com`），文本正常；**该资源包下所有 qwen VL 视觉模型实测 404 未开通** |

### 3.2 推荐方案（采纳"qwen 方向"+ 免单独配置）

**方案 A（首选）：百炼 qwen-vl 系列，复用主 LLM 凭据**

- OCR 端点与主 LLM 相同（compatible-mode/v1），**复用 `llm_config.json` 的 api_key** —— 满足"LLM 设置对话框不需要单独填写 OCR 模型"。
- OCR 模型名不进 UI：后端常量 `OCR_MODEL`，env 可覆盖（默认 `qwen-vl-plus`），前端无感。
- **前置动作（需要你操作）**：当前百炼资源包未含视觉模型（实测 404）。需在阿里云百炼控制台为该 key 开通 `qwen-vl-plus`（便宜、够用）或 `qwen-vl-max`（更强手写识别）。**开通前 OCR 功能保持灰置**。
- 备选镜像：若百炼开通受限，同代码兼容 DashScope 国际版 / OpenRouter 的 `qwen/qwen2.5-vl-72b-instruct`（仅换 base_url + 模型名 + 走已有 openrouter_key），切换只需改 env。

**方案 B（兜底，零新增开通）：OpenRouter 修复级联**

- 仅当方案 A 未开通时临时启用：把级联表换成 OpenRouter 上存活的 VL 模型（`qwen/qwen2.5-vl-72b-instruct` → `google/gemini-2.0-flash-exp:free`），并加 **401/402/429 时直接跳过后续重试**的快速失败（现状是 4 个模型串行 35s 全失败，体验极差）。

### 3.3 识别管线（方案 A 落地后的调用流程）

```
PDF  → pypdfium2 逐页渲染 200dpi PNG          （纯 pip 依赖，免系统 poppler）
图片 → 直接用；>3MB 先缩边 2000px 再 base64
  ↓
逐页调用 VL 模型（复用主 LLM key；prompt 强约束见下）
  ↓
页文本按顺序拼接（页间插 \n\n--- page N ---\n\n）
  ↓
文本清洗（§3.4） → 结构化抽取（§4，qwen3.8-flash 文本模型即可）
  ↓
写 meta.json + 入库
```

**Prompt 模板**（修掉历史发现的"解释性前言"问题）：

```
You are an OCR engine. Transcribe ALL text in this exam/homework page
word-for-word: printed questions, options, marks like [3 marks], and ALL
handwritten answers. Keep original order, question numbers, line breaks and
math symbols. Do NOT describe the image. Do NOT add any preamble, comment,
or translation. Output ONLY the transcription.
```

- 输出侧再兜一层：若首行匹配 `^(The image|Here is|This (image|page)|Sure)` 等前言模式则剥除。
- `max_tokens=4000`，`temperature=0`；单页超时 90s。

### 3.4 文本清洗规则（确定性代码，不依赖 LLM）

1. 剥前言/后语（上述正则 + 尾部 "Let me know if..." 类模式）。
2. 归一空白：行尾空白删除、3+ 空行压缩为 1、全角空格归一。
3. 页码行独立成行且 ≤4 字符纯数字 → 删除（渲染页脚噪声）。
4. Markdown 痕迹：`**bold**` 保留（前端渲染），但 `◻`/`□` 等装饰符清掉。
5. 保留原始页文本数组（`meta.pages[]`），清洗只发生在"拼接全文"视图，可回溯。

---

## 4. 结构化字段抽取

**两段式**：VL 模型只做转写（不抽取），抽取交给**主 LLM `qwen3.8-flash`（文本）**——便宜、快、且把视觉与抽取解耦，任何一环失败可独立重试。

抽取目标（JSON mode / 严格 prompt + `response_format` 若端点支持）：

| 字段 | 说明 | 示例 |
|---|---|---|
| `subject` | 科目，枚举 Maths/FMaths/Physics/Chemistry/Biology/English/History/Drama/CS/Other | Chemistry |
| `doc_type` | homework / report / exam / quiz | homework |
| `title` | ≤80 字标题（从题头/标题行生成） | Electrolysis homework |
| `date` | 作业/考试日期（找不到→上传日） | 2026-05-30 |
| `score` | 分数对象 `{got, total}` 或 null | {got: 7, total: 9} |
| `topics` | 知识点标签 ≤5 个 | ["electrolysis", "half-equations"] |
| `mistakes` | 错题列表：`{question_no, wrong_answer, correct_answer, reason}`，无错→空数组 | … |
| `weakness_summary` | 一句话弱点总结 ≤120 字 | … |

- 抽取失败（JSON 解析失败/超时）：**不阻塞入库**——全文与文件已保存，字段留空标 `extract_status=failed`，前端表单可手填，提供「重新抽取」按钮。
- 与错题本联动：`mistakes[]` 一键转错题条目写入 `errors_<child>.json`（复用 `/api/error-save` 的字段结构）。

---

## 5. 数据模型与入库表结构

延续项目 **JSON 文件存储**惯例（无数据库，原子写 + mtime 缓存已成熟），不引入 SQLite。

### 5.1 `uploads_index.json`（列表视图，轻）

```json
[
  {
    "id": "20261014-173000-a1b2c3",
    "child": "diya",
    "original_name": "chem_hw_0530.pdf",
    "doc_type": "homework",
    "subject": "Chemistry",
    "title": "Electrolysis homework",
    "date": "2026-05-30",
    "score": {"got": 7, "total": 9},
    "topics": ["electrolysis"],
    "page_count": 4,
    "size": 1902531,
    "status": "done",              // queued|uploading|ocr|extract|done|failed
    "ocr_status": "done",           // pending|done|failed
    "extract_status": "done",       // pending|done|failed|manual
    "error_model": "qwen-vl-plus",
    "created": "2026-10-14T17:30:00",
    "updated": "2026-10-14T17:32:11"
  }
]
```

### 5.2 `uploads/<child>/<ym>/<id>/meta.json`（详情，重）

```json
{
  "id": "…", "child": "diya", "original_name": "chem_hw_0530.pdf", "mime": "application/pdf",
  "pages": [
    {"no": 1, "image": "page-01.png",
     "raw_text": "（VL 原始输出）",
     "clean_text": "（清洗后）",
     "ocr_model": "qwen-vl-plus", "ocr_ms": 21340}
  ],
  "full_text": "…拼接清洗后全文…",
  "extract": {"subject": "…", "mistakes": ["…"], "weakness_summary": "…", "model": "qwen3.8-flash"},
  "status": "done", "retry_count": 0, "error": null,
  "created": "…", "updated": "…"
}
```

### 5.3 API 设计

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/upload/<child>` | multipart 上传单文件，返回 upload_id，同步完成存盘+页渲染；OCR/抽取异步 |
| GET | `/api/uploads/<child>?status=&subject=` | 列表（读 index） |
| GET | `/api/uploads/<child>/<id>` | 详情（含全文/页文本/抽取结果） |
| PUT | `/api/uploads/<child>/<id>` | 手工修正字段 / 触发重试（`{"action":"retry_ocr"}`） |
| DELETE | `/api/uploads/<child>/<id>` | 删目录 + 索引（需确认，前端二次确认） |
| GET | `/api/uploads/<child>/<id>/file/<name>` | 页图/原文件预览（白名单文件名，防目录穿越） |
| POST | `/api/uploads/<child>/<id>/to-errors` | 把 mistakes[] 批量转错题本条目 |

**异步执行**：gunicorn 4 worker 环境用**线程池**（`concurrent.futures.ThreadPoolExecutor(max_workers=2)`，进程内即可，上传后立刻返回 id，前端轮询状态）。不引入 celery/redis——家庭应用不值得多运维一栈。⚠️ 约束：OCR 属 IO 密集，2 并发 + 90s/页超时下 20 页 PDF 最坏 ~7 分钟，UI 必须有明确进度（§7）。

---

## 6. 识别失败与重复上传处理

### 6.1 失败处理（分层降级，永不静默丢文件）

| 失败点 | 策略 | 用户看到 |
|---|---|---|
| 上传中断/超限 | 即时 4xx，不建目录 | 红字原因（超大小/页数） |
| PDF 渲染失败（加密/损坏页） | 跳过坏页继续，`render_errors=[页码]` | ⚠️ "第 3 页无法解析，其余已识别" |
| VL OCR 失败 | 指数退避重试 2 次（30s/90s）→ 整件标 `ocr_status=failed`，**原文件保留** | 队列红色徽标 +「重试识别」按钮 |
| OCR 结果疑似空/过短（<20 字符/页） | 视同失败（可能是空白页或模型拒绝），进重试 | 同上 |
| 字段抽取失败 | 不阻塞；全文照常入库，`extract_status=failed` | 表单可手填 +「重新抽取」 |

**重试实现**：`PUT {"action":"retry_ocr"}` 重置状态机，只重跑失败页（`pages[].no` 过滤），不重复渲染/上传。

### 6.2 重复上传检测（双保险）

1. **硬去重**：上传时计算 SHA-256，同 child 下命中 → 直接 409 返回已存在条目链接（**不重复识别**，省 token）。
2. **软提示**：同 child + 同科目 + 文件名相似（归一后前 20 字符相同）且 7 天内 → 200 接受但返回 `similar_to` 提示，前端弹"可能重复，仍要上传？"确认框。
3. 误判兜底：409 响应里带 `force=1` 参数重传可强制（前端确认框的"仍要上传"）。

---

## 7. 任务状态与进度反馈

**状态机**（meta.status，单向推进，失败可回跳重试）：

```
queued → ocr → extract → done
            ↘ failed（可 retry_ocr 回到 ocr）
```

**进度粒度**（前端轮询 `GET /api/uploads/<child>/<id>`，间隔 2s，指数退避至 5s）：

- PDF 阶段：`ocr_progress = 已识别页数/总页数`（逐页写 meta，轮询即见）
- 队列行内嵌进度条 + 页码计数（"识别中 3/12 页"）
- 全部完成：绿标 + 抽取结果摘要（科目/分数/错题数），点击展开预览
- 失败：红标 + 具体原因（哪个模型、什么错误）+ 重试按钮

**SSE 备选**：项目已有 `/api/chat/stream` 的 SSE 基建（`_sse()`），若轮询体验不佳，v1.1 可加 `GET /api/uploads/<id>/events` 推送进度——v1 先轮询（实现简单、gunicorn 线程占用少）。

---

## 8. 权限与数据隔离

### 8.1 复用任务 2 的应用层鉴权

- 全部 `/api/upload*` 路由在 `before_request` 的 401 门内自动受保护，**无需新增鉴权代码**。
- 上传接口额外限制：同 IP 上传频率 ≤30 文件/小时（防滥用；家庭场景足够）。

### 8.2 孩子数据隔离

| 层 | 规则 |
|---|---|
| 存储 | 物理目录隔离：`uploads/diya/**` 与 `uploads/jemma/**` 永不混放；index 条目带 `child` 字段 |
| API | 路径级隔离：`/api/upload/diya` 只写 diya；**child 白名单校验**（`{'diya','jemma'}` 之外一律 400），杜绝路径注入 |
| 前端 | `app.html` 上传页 child 固定 `diya`（隐藏选择器）；`jemma.html` 固定 `jemma` —— 与现有"每孩子独立页面"模式一致，页面上没有跨孩子切换入口 |
| 导出/清理 | DELETE 与后续导出只允许操作自身 child 路径（服务端按 child 参数拼路径，拼接前过滤 `..`/`/`） |

### 8.3 文件安全

- 磁盘文件名一律 upload_id / page-NN.png（原名只存 meta），**彻底消除路径注入**。
- 预览接口文件名白名单正则 `^(original\.\w+|page-\d{2}\.png|meta\.json)$`。
- MIME 嗅探（file header）优先于扩展名：声明 .jpg 但实际是 HTML → 拒收。
- nginx 对 `uploads/` 静态访问关闭（一律走带鉴权的 API 预览），延续 `nginx-git-protect.conf` 思路。

---

## 9. 实施切分（供后续排期）

| 阶段 | 内容 | 依赖 |
|---|---|---|
| M1 | 上传/存储/列表/详情/删除 + 重复检测（无 OCR，文件可传可看） | 无 |
| M2 | OCR 管线（PDF 渲染 + VL 识别 + 清洗）+ 异步线程池 + 进度轮询 | **百炼开通 qwen-vl**（方案 A）或切换方案 B |
| M3 | 字段抽取 + 错题本联动 + 前端结果预览表单 | M2 |
| M4 | jemma.html 对齐 + 备份策略调整 + nginx 调整 | M1–M3 |

**M2 前置阻塞项**：需要你在阿里云百炼控制台确认该 API key 可开通视觉模型（当前实测全部 404）。开通前可先用方案 B（OpenRouter qwen2.5-vl）跑通全链路，随后一行 env 切回百炼。
