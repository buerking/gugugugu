# sub-push 后端设计说明（订阅 / 命中上报）

面向：后端服务端程序员。  
配套契约：`config/api_contracts/`。  
脚本只做两件事：**拉「本轮要跑的订阅」** → **搜完把命中结果列表回传**。用户信息、通知、启停状态全部由后端自己处理，**不下发给脚本、也不要求脚本上报**。

---

## 1. 数据流（极简）

```
后端 DB（subscription 表）
        │
        │  GET /api/sub-push/subscriptions
        │  只返回「本轮需要跑」的订阅列表（后端已过滤停用/过期等）
        ▼
   sub-push 脚本（搜索 + 本地去重）
        │
        │  POST /api/sub-push/push-batch
        │  body = 按 subscription_id 分组的命中商品 list
        ▼
后端：按 subscription_id 入库 → 自行关联用户 → 自行通知
```

原则：

1. **批量**：禁止逐条商品 HTTP。
2. **关联键只有 `subscription_id`**：脚本不需要、也不应拿到用户邮箱/手机等。
3. **幂等**：同一 `(subscription_id, item_key)` 重复上报不重复入库（或不重复通知），由后端保证。
4. **通知不在脚本侧**：文档不设计通知表；后端爱怎么通知怎么通知。

---

## 2. 数据表（脚本相关只需要一张主表）

### 2.1 `subscription` — 订阅记录（权威源）

一行 = 一条要搜索的订阅。用户归属、邮箱、通知渠道等若业务需要，**放在你们自己的用户/订单体系里**，用内部 FK 关联即可；**不要出现在脚本接口里**，也不必为脚本单独建 `sub_user` 表。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | BIGINT PK | 内部自增 |
| `subscription_id` | VARCHAR(64) UK | **对外主键**，下发与回传唯一关联键 |
| `user_id` | … | **仅后端内部用**（关联你们用户表）；**不下发脚本** |
| `match_type` | VARCHAR(16) | `keyword` / `seller` |
| `keyword` | VARCHAR(255) | 关键词；seller 型可空 |
| `seller_id` | VARCHAR(64) | 卖家订阅时用 |
| `web_id` | VARCHAR(32) | 卖家站点 ID，如 `mercari` / `paypay`（不用 web_name） |
| `sources` | JSON | 关键词型站点列表，如 `["mercari","paypay"]` |
| `category_id` / `brand_id` | VARCHAR(64) | 可空 |
| `price_min` / `price_max` | INT | 可空，0/NULL=不限 |
| `exclude_keyword` | VARCHAR(255) | 可空 |
| `created_time` | BIGINT | 可选，首轮时间窗参考 |
| `enabled` 等 | … | **仅后端内部过滤用**；接口不返回 status，脚本假定收到的都是要跑的 |

索引：`uk_subscription_id`；内部按用户查可用 `idx_user (user_id)`。

**不要**：

- 向脚本暴露 `email` / `phone` / `notify_channels` / `sub_user_id`
- 在下发报文里带「全部订阅再让脚本自己滤 status」——后端只返回待跑集合

### 2.2 `subscription_hit` — 命中商品（建议）

脚本上报的是「哪些 `subscription_id` 命中了哪些商品」的 list；后端落库后给 App 列表用。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | BIGINT PK | |
| `subscription_id` | VARCHAR(64) | 关联订阅 |
| `item_key` | VARCHAR(128) | `source_site:item_id`，幂等 |
| `source_site` | VARCHAR(32) | |
| `item_id` | VARCHAR(64) | |
| `item_url` | VARCHAR(512) | |
| `title` | VARCHAR(512) | 展示 |
| `price` | INT | 展示 |
| `thumbnail` | VARCHAR(1024) | 展示封面 URL |
| `currency` | CHAR(3) | 默认 JPY |
| `listing_type` / `status` | VARCHAR | 可空 |
| `item_created` / `item_updated` | BIGINT | 可空 |
| `matched_at` | BIGINT | 脚本命中时间 |
| `created_at` | DATETIME | |

```sql
UNIQUE KEY uk_sub_item (subscription_id, item_key)
```

用户是谁、发不发通知：入库后用 `subscription.user_id` 自行处理，**不需要脚本参与**。

### 2.3 建表示例（MySQL，精简）

```sql
CREATE TABLE subscription (
  id               BIGINT PRIMARY KEY AUTO_INCREMENT,
  subscription_id  VARCHAR(64)  NOT NULL,
  user_id          BIGINT NULL COMMENT '仅后端内部，不下发脚本',
  match_type       VARCHAR(16)  NOT NULL DEFAULT 'keyword',
  keyword          VARCHAR(255) NULL,
  seller_id        VARCHAR(64)  NULL,
  web_id           VARCHAR(32)  NULL COMMENT 'mercari / paypay 等',
  sources          JSON NULL,
  category_id      VARCHAR(64)  NULL,
  brand_id         VARCHAR(64)  NULL,
  price_min        INT NULL,
  price_max        INT NULL,
  exclude_keyword  VARCHAR(255) NULL,
  created_time     BIGINT NULL,
  enabled          TINYINT NOT NULL DEFAULT 1,
  created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_subscription_id (subscription_id),
  KEY idx_enabled (enabled),
  KEY idx_user (user_id)
);

CREATE TABLE subscription_hit (
  id               BIGINT PRIMARY KEY AUTO_INCREMENT,
  subscription_id  VARCHAR(64)  NOT NULL,
  item_key         VARCHAR(128) NOT NULL,
  source_site      VARCHAR(32)  NOT NULL,
  item_id          VARCHAR(64)  NOT NULL,
  item_url         VARCHAR(512) NOT NULL,
  title            VARCHAR(512) NOT NULL,
  price            INT NOT NULL DEFAULT 0,
  currency         CHAR(3) NOT NULL DEFAULT 'JPY',
  thumbnail        VARCHAR(1024) NULL,
  listing_type     VARCHAR(16) NULL,
  status           VARCHAR(32) NULL,
  item_created     BIGINT NULL,
  item_updated     BIGINT NULL,
  matched_at       BIGINT NOT NULL,
  created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_sub_item (subscription_id, item_key),
  KEY idx_sub_matched (subscription_id, matched_at)
);
```

---

## 3. 以前文档里「快照 / 上报批次表」是干什么的？（结论：不必建）

| 概念 | 原先意图 | 现在结论 |
|------|----------|----------|
| **下发快照 snapshot** | 给某次拉取打版本号，方便对账「脚本拿的是哪一版订阅」 | 脚本**每轮都重新拉**接口；后端只返回当前要跑的。无需单独快照表，也无需 `snapshot_id`。 |
| **push_batch 批次表** | 用 `batch_id` 整包幂等，防同一批重复处理 | 有 `(subscription_id, item_key)` 唯一约束已够用。请求里可带 `run_id` 方便日志，**不必建批次表**。 |

---

## 4. 接口设计

Base：`/api/sub-push`  
鉴权：`Authorization: Bearer <token>`。

### 4.1 `GET /api/sub-push/subscriptions`

返回**本轮要跑的订阅 list**（已过滤停用等）。**无用户信息字段**。

```json
{
  "version": 3,
  "generated_at": 1761789600,
  "subscriptions": [
    {
      "subscription_id": "sub_10001",
      "match_type": "keyword",
      "keyword": "千恋万花",
      "sources": ["mercari", "paypay"],
      "category_id": "75",
      "brand_id": "",
      "price_min": 300,
      "price_max": 500,
      "exclude_keyword": ""
    },
    {
      "subscription_id": "sub_20001",
      "match_type": "seller",
      "web_id": "mercari",
      "seller_id": "488768874"
    }
  ]
}
```

字段约定：

- `match_type=keyword`：必有 `subscription_id`、`keyword`；`sources` 默认可由后端填或脚本用配置默认值。
- `match_type=seller`：必有 `subscription_id`、`web_id`、`seller_id`。
- `web_id` 枚举：
  - `mercari`：煤炉卖家（数字 ID）
  - `paypay`：雅虎闲置卖家（如 `p10021425`）
  - `yahoo_auction`：雅虎拍卖卖家（卖家页路径 `aucUserId`，如 `4VhAdiaCsN7Mw7skrNTKDMxdpnfP2`）
- 关键词若要搜雅虎两站，在 `sources` 里分别写 `paypay` / `yahoo_auction`（与煤炉独立，不会自动带出）。
- **不要**返回：`email`、`phone`、`notify_channels`、`sub_user_id`、`status`。

### 4.2 `POST /api/sub-push/push-batch`

脚本告诉后端：**哪些订阅 ID 命中了哪些商品**（一个 list / 集合）。

```json
{
  "schema_version": 2,
  "run_id": "run-1761790000",
  "generated_at": 1761790123,
  "client": "sub-push",
  "results": [
    {
      "subscription_id": "sub_10001",
      "item_count": 2,
      "items": [
        {
          "title": "示例商品标题",
          "price": 3500,
          "thumbnail": "https://static.mercdn.net/item/detail/orig/photos/m95525968401_1.jpg",
          "item_id": "m95525968401",
          "item_key": "mercari:m95525968401",
          "item_url": "https://jp.mercari.com/item/m95525968401",
          "source_site": "mercari",
          "currency": "JPY",
          "listing_type": "buy_now",
          "status": "on_sale",
          "created": 1761789000,
          "updated": 1761789000,
          "matched_at": 1761790123
        }
      ]
    }
  ]
}
```

说明：

- 顶层是 **`results` 数组**：每个元素 = 一个 `subscription_id` + 其命中 `items`。
- 商品上**不再带** `sub_user_id` / `keyword` / 用户联系方式（关键词若要展示，后端用 `subscription_id` 查库即可；脚本也可选在 item 里带 `keyword` 作冗余，非必须）。
- 无命中时：可不调接口，或 `results: []`。
- 处理：遍历 `results` → 对每条 item `INSERT ... ON DUPLICATE KEY (subscription_id, item_key)` → 后端自行决定通知。

**响应建议：**

```json
{
  "ok": true,
  "accepted_item_keys": ["mercari:m95525968401"],
  "rejected": [
    {
      "subscription_id": "sub_999",
      "item_key": "mercari:m000",
      "reason": "subscription_not_found"
    }
  ]
}
```

HTTP 2xx 且 `ok != false`：脚本视为成功；若带 `accepted_item_keys` 则只吸收这些 key。

---

## 5. 职责划分

| 事项 | 脚本 | 后端 |
|------|------|------|
| 谁启用/停用订阅 | 不管 | 过滤后只下发待跑 |
| 用户邮箱/通知 | 不管 | 自理 |
| 搜索与本地去重水位 | 负责（按 subscription_id） | 不管 |
| 命中长期存档 / App 列表 | 上报 list | 入库 |
| 通知用户 | 不管 | 自理 |

---

## 6. 联调清单

1. 库中插入带 `subscription_id` 的订阅（enabled）。  
2. `GET /subscriptions` 只返回这些行，无用户字段。  
3. 脚本跑一轮后 `POST` 的 `results[].subscription_id` 能对上。  
4. 重复上报同一 `(subscription_id, item_key)` 不重复插（或通知幂等）。  
5. App 列表用 hit 表的 `title` / `price` / `thumbnail` / `item_url`。

---

## 7. 相关文件

| 路径 | 用途 |
|------|------|
| `config/api_contracts/subscriptions.v3.json` | 下发订阅 list 样例 |
| `config/api_contracts/push_batch.request.json` | 上报 results 样例 |
| `config/api_contracts/push_batch.response.json` | 上报响应样例 |
| `config/api_contracts/README.md` | 契约摘要 |
| 本文档 | 表 + 接口 |

---

## 8. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-10-30 | 初版 |
| 2026-10-30 | 按产品意见简化：去掉脚本侧用户表/用户字段/通知/快照表/批次表；`web_name`→`web_id`；上报改为按 `subscription_id` 的 results list |
