# GLaDOS 自动签到

一个仅使用 Python 标准库的轻量签到任务。支持 Cookie 鉴权、超时、瞬时故障重试、随机延迟、脱敏日志和 GitHub Actions 定时执行。

> GLaDOS 没有公开稳定的签到 API 文档。本项目使用常见的内部接口作为默认值，并允许通过环境变量覆盖。请遵守站点规则；出现验证码或风控时应改为人工处理。

## 本地运行

需要 Python 3.10 或更高版本。

1. 复制 `.env.example` 为 `.env`。
2. 登录 GLaDOS，在浏览器开发者工具的 Network 中手工签到一次。
3. 从签到请求的 `Cookie` 请求头复制值，填入 `.env` 的 `GLADOS_COOKIE`。
4. 执行：

   ```bash
   python main.py
   ```

程序不会输出 Cookie。退出码含义：`0` 成功或已签到，`1` 配置错误，`2` Cookie 失效，`3` 接口变化或请求被拒绝，`4` 网络故障。

## GitHub Actions 部署

1. 将项目推送到你自己的私有 GitHub 仓库。
2. 打开仓库的 **Settings → Secrets and variables → Actions**。
3. 新建 Repository secret：名称 `GLADOS_COOKIE`，值为完整 Cookie。
4. 在 **Actions → GLaDOS check-in → Run workflow** 手工执行一次并检查结果。
5. 验证成功后，工作流会每天北京时间 09:17 左右执行。GitHub 的定时任务可能延迟。

建议使用私有仓库。不要把真实 Cookie 写进 `.env.example`、源代码、提交记录或 issue。

## 接口发生变化

再次手工签到并查看 Network 请求，然后通过 Secrets 或环境变量覆盖：

```env
GLADOS_BASE_URL=https://glados.rocks
GLADOS_CHECKIN_PATH=/api/user/checkin
GLADOS_CHECKIN_TOKEN=glados.one
```

如果请求体不再是 `{ "token": "..." }`，则需要相应修改 `glados_checkin/client.py`。

## 测试

```bash
python -m unittest discover -s tests -v
```
