# 密钥保护设计

## 原则

- **Git 只存结构与空模板，不存真实密钥。**
- 运行时密钥仅存在本机，路径固定且被 `.gitignore` 屏蔽。

## 文件分层

| 文件 | 是否入库 | 用途 |
|------|----------|------|
| `config/config.example.json` | 是 | 公开配置结构模板 |
| `config/secrets.example.json` | 是 | 密钥字段清单（值留空） |
| `config/config.json` | **否** | 本机非敏感/业务参数 |
| `config/secrets.local.json` | **否** | 本机真实密钥（唯一真相源） |
| `config/dpop_es256_key.pem` | **否** | DPoP 私钥 |
| `config/edi_credentials.json` | **否** | EDI 登录 |
| `config/subscriptions.json` | **否** | 含客户信息 |

`sub-push` 与 `big` 均按此约定。

## 加载顺序

```
config.json  +  secrets.local.json（深度合并覆盖）  →  运行时 CONFIG
```

GUI / `save_config` 会自动把密钥字段写回 `secrets.local.json`，公开字段写回 `config.json`。

## 新环境部署

```bat
cd sub-push\config
copy config.example.json config.json
copy secrets.example.json secrets.local.json
:: 编辑 secrets.local.json 填入真实值

cd ..\..\big\config
copy config.example.json config.json
copy secrets.example.json secrets.local.json
copy edi_credentials.example.json edi_credentials.json
```

## 提交前自检

```bat
python scripts\check_no_secrets.py --staged
```

若检查失败，不要 `git commit`；把密钥挪回 `secrets.local.json`。
