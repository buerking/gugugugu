# sub-push API 数据契约

定时脚本与后端约定（批量，禁止逐条商品 HTTP）。

**端到端工程说明（对接现网 jpkix 接口）**：见 [`docs/SUBSCRIPTION_SERVICE_GUIDE.md`](../../docs/SUBSCRIPTION_SERVICE_GUIDE.md)。  
**早期自拟表结构草案**：见 [`docs/BACKEND_SUBSCRIPTION_DESIGN.md`](../../docs/BACKEND_SUBSCRIPTION_DESIGN.md)（以工程说明与后端正式文档为准）。

脚本**不处理用户信息与通知**；关联键是 `subscriptionId`。  
现网对接：`config.json` → `subscription_api`（`enabled` 默认 false，后端就绪后打开）。

| 文件 | 方向 | 说明 |
|------|------|------|
| `subscriptions.v3.json` | 入站 | 待跑订阅 **list**（无用户字段）；`GET /api/sub-push/subscriptions` |
| `subscriptions.v2.json` | 入站(旧) | 按用户嵌套的旧样例，仅兼容本地占位；正式接口用 v3 |
| `push_batch.request.json` | 出站 | `results[]`：每个元素 = `subscription_id` + 命中 `items` |
| `push_batch.response.json` | 出站响应 | `ok` + 可选 `accepted_item_keys` |

幂等键（后端）：`subscription_id` + `item_key`（`source_site:item_id`）。

## 搜索并发池

`config.search_pool`：
- `per_worker_capacity`：每条线程队列最多处理几个搜索单元（默认 5）
- `serial_below_or_equal`：任务数 ≤ 该值时串行，不开线程池（默认 5）
- `max_workers`：最多开几条队列/线程（默认 8，建议 3～8）

AI 识别不在 sub-push；仅 big 项目使用。

## 内存

- 搜索归一化后立刻丢掉 `raw`，只保留有价值字段（`item_slim`）。
- **上报商品字段**：`title`、`price`、`thumbnail`、`item_id`、`item_key`、`item_url`、`source_site`；分组键 `subscription_id`。
- 入站每条订阅 **应带 `subscription_id`**；本地占位无 ID 时脚本生成稳定伪 ID。
- 卖家站点字段用 **`web_id`**（兼容旧字段 `web_name`）。

## 上架时间与漏抓补回

- 各站搜索按**最新上架**排序；本地再用 `created_after` 时间窗过滤（见 `config.time_window`）。
- 任务水位存在 SQLite：仅在搜索全成功且（无新货或推送已吸收）后推进。
- 进程卡死 / 推送失败：**不写 seen、不推水位** → 下一轮从旧水位补抓。
