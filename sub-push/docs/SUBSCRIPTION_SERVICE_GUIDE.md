# 上新订阅服务 · 工程说明（给脚本 / 联调）

一句话：**后端管「订了什么、通知谁」；脚本只管「按时去网站搜新货，再把结果交回后端」。**

---

## 1. 整件事怎么转

```
① 用户在 App/后台付钱开通订阅
        ↓
② 后端记下：订哪个站、关键词还是卖家、价格、从何时起算新货
        ↓
③ 脚本定时醒来
        ↓
④ 向后端「翻页要任务」→ 拿到本轮所有生效订阅
        ↓
⑤ 按每条订阅去对应网站搜 → 筛出新上架
        ↓
⑥ 把命中商品「打包交回」后端
        ↓
⑦ 后端入库（重复的自动忽略）
        ↓
⑧ 后端自己的定时任务发推送/邮件（脚本不管）
```

脚本运行多勤，由脚本自己定（例如每几分钟一轮）。  
同一件商品多传几次也没关系，后端会去重。

---

## 2. 两个接口（长得一样，只是名字不同）

| 干什么 | 接口名 |
|--------|--------|
| 要任务（下发订阅） | `get-search-subscriptions` |
| 交作业（回传命中） | `push-search-subscription-hits` |

**地址（生产）**

```
POST https://p.jpkix.com/yii/web/index.php?r=home/api/{接口名}
```

**怎么传**

- Content-Type：`application/x-www-form-urlencoded`
- 表单只有两个字段：
  - `data` = 参数的 **JSON 字符串**（原样）
  - `sign` = `md5("data=" + data原文 + "&key=purchaseApi")` 再转大写

**签名口诀**

1. 先定好 `data` 字符串  
2. 用**这一份**去算 `sign`  
3. 提交时 `data` **一个字符都不能改**（不要签完又重新 `json.dumps`）

示例：

```
data = {"lastId":0}
待签名 = data={"lastId":0}&key=purchaseApi
sign  = 27117605015294652A420D62D0762756
```

统一返回壳：

```json
{ "success": true, "message": "...", "data": { ... }, "code": 0 }
```

签名错：`success=false, code=403`。

---

## 3. 第一步：要任务（翻页拉订阅）

**请求 data**

```json
{ "lastId": 0 }
```

**每一轮**都从 `lastId=0` 开始；用返回的 `nextLastId` 继续拉，直到 `hasMore=false`。  
每页最多 1000 条。本轮拉完后新开通的，下一轮才看得到。

**一条订阅长这样（简化）**

| 字段 | 意思 |
|------|------|
| `subscriptionId` | 订阅编号，回传时原样带回 |
| `matchType` | `1`=关键词，`2`=卖家 |
| `webId` | **只对应一个网站**（数字，见下表） |
| `keyword` | 关键词；卖家订阅为空串 |
| `sellerId` | 卖家/店铺 ID；关键词订阅为空串 |
| `priceMin` / `priceMax` | 日元；`0`=不限；卖家订阅恒为 0 |
| `startTime` | unix 秒；**早于此时上架的不要回传** |

**网站编号（webId）**

| webId | 网站 | 脚本里怎么搜 |
|------|------|----------------|
| 28 | 煤炉 | 关键词 / 卖家：**一口价+拍卖同一套搜**；类型看商品字段/详情，不拆两次搜。卖家纯数字→`seller_id`，非纯数字→`shop_id` |
| 846 | 煤炉商城 | 按 `shop_id` 查（仍走煤炉） |
| 2733 | 煤炉拍卖（业务标签） | **与 28 同一套煤炉搜索**，回传仍带 webId=2733 |
| 324 | PayPay 闲置 | 关键词 / `sellerIds` |
| 988 | 雅虎拍卖 | 关键词 / `aucUserIds`（卖家 ID 比较不区分大小写） |

重要：**一条订阅只打一个站。**  
若用户又订煤炉又订闲置，后端会下发两条订阅，不是一条里带多个 `sources`。

---

## 4. 第二步：脚本自己搜、自己筛

后端**不会**再帮你滤条件。回传前脚本必须做完：

1. 去对的网站搜  
2. 关键词要对得上（卖家订阅则按卖家/店铺）  
3. 价格在 `priceMin`～`priceMax` 内（0 表示那头不限）  
4. 上架时间 **晚于** `startTime`  
5. 再交给回传接口  

本地还可以做「见过的不重复搜/不重复传」来省流量，但**不是必须**：后端入库也会去重。

---

## 5. 第三步：交作业（回传命中）

**请求 data 形状**

```json
{
  "results": [
    {
      "subscriptionId": 12,
      "items": [
        {
          "webId": 28,
          "goodsCode": "m12345678901",
          "title": "商品名称",
          "price": 25000,
          "itemUrl": "https://jp.mercari.com/item/m12345678901",
          "thumbnail": "https://static.mercdn.net/xxx.jpg"
        }
      ]
    }
  ]
}
```

**硬规则（背下来）**

- 每件商品 **6 个字段全要**：`webId` / `goodsCode` / `title` / `price` / `itemUrl` / `thumbnail`
- 值只能是字符串或整数（别传 true/false）
- `webId` 必须等于这条订阅的 `webId`
- `goodsCode`：站点商品 ID（拍卖用拍卖 ID）；只含字母数字 `_` `-`；最长 64
- `itemUrl` / `thumbnail`：必须 http(s)
- 一次最多 **1000 个订阅组、合计 5000 件**；超了整批拒绝 → **分批再传**

**返回在说什么**

每组会告诉你：`accepted`（新入库）/ `duplicated`（以前传过）/ `discarded`（丢掉）以及丢掉原因。  
`duplicated` 不算失败。`db_error` 可以之后重传。

---

## 6. 和「我们以前设计的脚本」差在哪（对齐表）

| 点 | 以前脚本设想 | 现在后端定稿 | 脚本要怎么改 |
|----|--------------|--------------|--------------|
| 拉订阅 | 读本地 JSON 或自拟 GET | 表单 POST + 签名 + **翻页 lastId** | 新写拉取客户端；可保留本地 JSON 作联调兜底 |
| 鉴权 | Bearer token | `data` + `sign`（key=`purchaseApi`） | 按文档签 |
| 订阅形态 | 一条可多站 `sources[]` | **一条一站 `webId` 数字** | 映射表：28/324/988… → mercari/paypay/yahoo_auction |
| 类型 | `"keyword"` / `"seller"` | `matchType` 1 / 2 | 转换一下 |
| 时间窗 | 本地水位 + created | 下发带 `startTime` | 以 `startTime` 为准（本地水位可继续用来少搜） |
| 回传字段 | `item_id`/`item_url`/`source_site`… | `goodsCode`/`itemUrl`/`webId`… | 组包时改名、截断、校验 |
| 回传外壳 | JSON body | 仍是 JSON，但放进表单字段 `data` | 先 dumps 成字符串再签名 |
| 通知 | 不管 | 不管 | 已一致 |
| 用户信息 | 不下发 | 不下发 | 已一致 |
| 去重 | 本地 seen + 后端 | 后端必做；本地可选 | 可保留本地 seen 降压 |

**脚本已对齐（等后端开测）**

1. `subscription_api`：`data`+`sign`、翻页下发、回传 6 字段分批（`src/pipeline/jpkix_api.py`）  
2. `webId` 映射与 28/2733 合搜（`src/web_sites.py`）  
3. 煤炉卖家：字段/详情判拍卖；非纯数字 / 846 走 `shop_id`  
4. 配置默认 `subscription_api.enabled=false`，后端好了改 `true`；**测试也用真实订阅/真实回传**，与正式同一套逻辑（最多只换 `base_url`）  

**说明**

- 未开 API 时仍可读本地 JSON（仅开发兜底，不是测试专用模式）  
- 多线程队列、本地 seen 去重仍保留（后端也会去重）  

---

## 7. 建议的脚本一轮流程（实现时照这个写）

```
开始一轮
  → lastId=0
  → while hasMore: 调 get-search-subscriptions，攒 all_subs
  → 把每条订阅变成「搜索任务」
       matchType=1 → 关键词任务（一个 webId）
       matchType=2 → 卖家任务（一个 webId + sellerId）
  → 多线程去搜
  → 过滤：时间 > startTime，价格区间，标题/卖家规则
  → 按 subscriptionId 装进 results[]
  → 按批（≤1000 组、≤5000 件）调 push-search-subscription-hits
  → 看返回：accepted/duplicated 正常；db_error 可记日志下轮重试
  → 睡 N 分钟，下一轮（再从 lastId=0 拉）
```

---

## 8. 联调 checklist

1. 找后端要：联调用的 `base_url`（可与生产同机或另机），以及 **真实已付款** 的关键词/卖家订阅  
2. `config.json` → `subscription_api.enabled=true`（`dry_run` 保持 false）；需要时只改 `base_url`  
3. 跑一轮：翻页拉订阅 → 搜索 → 回传；再跑一轮同一商品应出现 `duplicated`  
4. 签名自检：`data={"lastId":0}` → sign=`27117605015294652A420D62D0762756`  
5. 超过 5000 件应自动拆批（脚本已按上限切）  

---

## 9. 给小学生版总结

- **后端**：发作业单（订阅）、收作业（商品）、发通知。  
- **脚本**：按时去拿作业单 → 上网找新货 → 把找到的交回去。  
- **说话方式**：所有话都塞进表单里的 `data`，再盖一个 `sign` 章。  
- **一条作业只去一个网站**；要去三个网站就有三张作业单。  
- **交作业要写全 6 个空**；写过的可以再交，老师（后端）会标「重复」但不骂你。  

测试环境地址和真实测试订阅，向后端同事要。
