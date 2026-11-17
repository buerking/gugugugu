# sub-push（统一守护）

公共订阅 → 中间层切片搜索 → 推 jpkix；VIP 与公共混编搜索，冷却期走 `big` 的 AI/邮件。

## 运行

在本目录：

```bash
python app.py
```

仓库根目录也可：`启动统一守护.bat` / `python unified_app.py`

## 配置

1. 复制 `config/config.example.json` → `config/config.json` 并填密钥  
2. 确认并列存在 `../big`，且 `big/config/config.json` 已就绪（`vip.big_root` 留空即可）  
3. DPoP 私钥：`config/dpop_es256_key.pem`（本地，不入库）

## 结构

- `src/unified_cycle.py`：切片调度 + VIP 队列  
- `src/subscription_middle.py`：订阅中间层  
- `src/vip/`：大客户加载 / AI 桥接 / 投递  
- `src/pipeline/`：搜索、seen、后端推送  
