# 主备灾备（HTTP health）

生产端对外提供 `/health`，返回**内存中**最近一次对外请求时间（不落盘）。  
开发端用生产机 IP 周期性请求；连不上，或最近请求过旧，则自动接管。

## 原理

```
生产机 ──每发搜索/推送──▶ 更新内存 last_request_at
       ◀── GET /health ── 开发机灾备监控
```

`/health` 示例：

```json
{
  "ok": true,
  "alive": true,
  "role": "primary",
  "node_id": "PROD-PC",
  "last_request_at": 1731830000.1,
  "last_request_at_iso": "2026-11-17T17:20:00",
  "last_request_kind": "curl",
  "last_request_detail": "GET https://...",
  "activity_age_seconds": 12.3,
  "stale_seconds": 180
}
```

判断：

| 情况 | 结果 |
|------|------|
| `http://生产IP:18765/health` 连不上（停电/进程挂） | 失联 → 接管 |
| 能连上但 `activity_age_seconds` > `stale_seconds` | 假活/卡死 → 接管 |
| 年龄正常 | 生产存活 |

## 配置

**生产机** `sub-push/config/config.json`：

```json
"failover": {
  "enabled": true,
  "role": "primary",
  "stale_seconds": 180,
  "http_health": {
    "enabled": true,
    "host": "0.0.0.0",
    "port": 18765
  }
}
```

Windows 防火墙放行入站 TCP `18765`。局域网确认开发机能 `ping` 到生产 IP。

**开发机**：

```json
"failover": {
  "enabled": true,
  "role": "standby",
  "stale_seconds": 180,
  "fail_checks_before_takeover": 2,
  "ok_checks_before_release": 2,
  "standby_primary_url": "http://192.168.x.x:18765/health"
}
```

把 `192.168.x.x` 换成生产机局域网 IP。

## 怎么跑

| 机器 | 做什么 |
|------|--------|
| 生产 | `启动统一守护.bat`（自动开 `/health`） |
| 开发 | 只开 `启动灾备监控.bat`，不要日常开统一守护 |

本机自测：

```bat
curl http://127.0.0.1:18765/health
```

开发机测生产：

```bat
curl http://生产IP:18765/health
```
