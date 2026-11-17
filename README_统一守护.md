# 统一守护（单仓库）

代码目录：`sub-push/`（VIP 依赖并列 `big/`）  
启动：双击本目录 `启动统一守护.bat`

## 一轮调度

1. 中间层刷新公共订阅，并与 VIP 混编切片
2. 按片并发搜索，公共结果推后端
3. VIP 命中进异步队列（AI + 邮件），与刷新互斥锁防 import 串包
4. 片间冷却；超时则立刻下一片/下一轮

## 并发探活

```bat
cd sub-push
python scripts\probe_concurrency.py
```
