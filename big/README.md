# big — 专供简易推送版

独立于 `sub-push` 的轻量脚本：三站搜索 → 时间/标题预过滤 → DeepSeek 审核 → 邮件 / 企业微信。

## 流程

```
订阅关键词
  → Mercari（DPoP 直连 api.mercari.jp，不经 jpkix）
  → PayPay / Yahoo 拍卖
  → 时间窗（created 或 updated ≥ 1.5×调度间隔）
  → 在售 + 型号边界匹配 + GPU 整机/配件启发式
  → 去重（data/seen_ids.json，含 meta 便于排查）
  → DeepSeek 对照 reference_items.md
  → 邮件（默认）/ 企业微信
```

首轮默认 **只建基线不推送**。之后每轮会把「已见/已推」写入 `data/seen_ids.json`，同一 `source:id` 不会再推。

### 三站「按新到旧」排序

| 站点 | 参数 |
|------|------|
| 煤炉 | `sort=SORT_CREATED_TIME` + `order=ORDER_DESC`（已默认） |
| PayPay 闲置 | `sort=openTime` + `order=DESC`（已默认） |
| 雅虎拍卖 | `sort=-FIRST_START_TIME`（API 合法值；无 NEWORDER） |

雅虎拍卖混入闲置：请求参数 **`sellerTypes`**（配置 `seller_types`）  
合法值：`AUCTION` / `AUC_STORE` / `FLEAMARKET`。默认 `["AUCTION","AUC_STORE"]` 排除闲置；本地仍会用 `isFleamarketItem` 兜底。

## 通知通道

订阅字段 `notify_channels`：`["email"]` / `["email","wecom"]` / `["wecom"]`。  
**默认建议 `email`**。

### 邮件

`config/config.json` → `email`：

- `smtp_host`：`smtp.163.com`，端口 `465` + `use_ssl=true`
- `smtp_user` / `smtp_password`：发件邮箱 + **SMTP 授权码**
- `from_name`：显示名（如「福利君」）
- 订阅 `user_email`：收件人

一封邮件可含多条链接：

- 标题：`【N件上新】标题前10字¥价格，…`
- 正文：每条 标题 / 价格 / 链接

> 煤炉已用 DPoP 直连 `api.mercari.jp`（`entities:search`），**不走 jpkix**。  
> 对照源：`../py_search_algo_script/push/sub-push` 仍是 jpkix + FTP/`notifier`；`big` 已独立。

### 企业微信

| 字段 | 说明 |
|------|------|
| `corp_id` / `corp_secret` | 管理后台企业 ID + 应用/客户联系 Secret |
| `agent_id` | 仅 `app` 模式 |
| `wecom_external_userid` | 微信客户 id（`external_contact`） |
| `wecom_chat_id` | **客户群** chat_id（`customer_group`） |
| `wecom_userid` | 跟进成员 / sender |
| `webhook_url` | **仅企业内部群**机器人 |

`wecom_mode`：

- `external_contact`：客户联系单聊群发（`add_msg_template`，可能需成员确认）
- `customer_group`：客户群群发（同上 API，`chat_type=group`）
- `webhook`：企业内部群机器人（**客户群不能加机器人**）
- `app`：应用消息给企业成员

文档参考：[客户联系](https://developer.work.weixin.qq.com/document/path/101837)、[群发消息](https://developer.work.weixin.qq.com/document/path/101844)、[获取客户群](https://developer.work.weixin.qq.com/document/path/101845)。

**客户群想“webhook 群发”？** 不行——群机器人只支持企业内部群。可选：

1. `wecom_mode=customer_group` + `wecom_chat_id`（客户群群发 API）
2. 建一个**内部群**拉员工，用 webhook；再由人转发客户群
3. 继续用邮件（最稳）

## 煤炉噪声优化（如 Quadro P4000）

预过滤会：

- 丢掉 `SOLD_OUT` / `TRADING`
- 型号边界：`P400` 不会当成 `P4000`
- 整机（Xeon/SSD/Win/工作站等）与支架配件剔除
- `created`/`updated` 时间窗（旧货近期更新也会进来，交给去重+AI）

AI 提示词见 `reference_items.md` → `## gpu_quadro`。

## 雅虎拍卖 Cookie

**不必填**。无 cookie 可正常搜索；偶发风控再补浏览器 Cookie。

## 快速开始

```bash
cd big
pip install -r requirements.txt
```

1. 填 `config/config.json` 的 `email`（及可选企微）
2. 编辑 `config/reference_items.md`、`config/subscriptions.json`
3. 运行：

```bash
python -m src.main
python app.py
```

## 订阅示例

```json
{
  "enabled": true,
  "keywords": ["quadro p4000", "quadro p2000"],
  "price_min": 0,
  "price_max": 0,
  "sources": ["mercari", "paypay", "yahoo_auction"],
  "customer_name": "GPU客户",
  "sub_user_id": "GPU01",
  "user_email": "you@qq.com",
  "notify_channels": ["email"],
  "wecom_mode": "external_contact",
  "wecom_external_userid": "",
  "wecom_chat_id": "",
  "reference_key": "gpu_quadro"
}
```
