# push（生产整合仓库）

统一上新守护：公共订阅推后端 + VIP（DeepSeek / 邮件），与 `big` 工程并列。

## 目录

```
push/
  sub-push/     # 统一守护（主进程）
  big/          # VIP AI / 邮件 / 大客户订阅配置
  unified_app.py
  启动统一守护.bat
```

## 启动

双击 `启动统一守护.bat`，或：

```bat
cd sub-push
python app.py
```

## 配置与密钥

- 结构：`config.json`（业务参数）+ `secrets.local.json`（密钥，**绝不入库**）
- 模板：`config.example.json` / `secrets.example.json`
- 说明见 [`docs/SECRETS.md`](docs/SECRETS.md)
- VIP 默认并列 `../big`；`vip.big_root` 留空即可

提交前建议：

```bat
python scripts\check_no_secrets.py --staged
```

## Git

仓库已 `git init` 并完成暂存；密钥文件已被 ignore。需要时再执行首次 `git commit`。
