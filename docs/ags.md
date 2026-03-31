# Tencent AGS (Agent Sandbox) Provider

腾讯云 AGS SWE 沙箱的 SWE-ReX Provider 和 Runtime 实现。

## 概述

AGS SWE 沙箱内置了 SWE-ReX Server，无需手动部署。本模块提供：

- **`TencentAGSDeployment`** — 管理沙箱实例生命周期（创建、连接、销毁）
- **`AGSRuntime`** — 通过数据面网关与沙箱内的 SWE-ReX Server 通信

## 安装

```bash
pip install swe-rex[ags]
```

或从源码安装：

```bash
uv pip install -e ".[ags]"
```

## 快速开始

```python
import asyncio
from swerex.deployment.ags import TencentAGSDeployment
from swerex.runtime.abstract import Command

async def main():
    deployment = TencentAGSDeployment(
        tool_id="sdt-xxxxxxxx",       # AGS 控制台创建的沙箱工具 ID
        image="swebench/sweb.eval.x86_64.django__django-16379:latest",
        # 凭据从环境变量 TENCENTCLOUD_SECRET_ID / TENCENTCLOUD_SECRET_KEY 读取
    )
    try:
        await deployment.start()
        result = await deployment.runtime.execute(
            Command(command="python --version", shell=True)
        )
        print(result.stdout)
    finally:
        await deployment.stop()

asyncio.run(main())
```

## 配置

### TencentAGSDeploymentConfig

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `tool_id` | `str` | `""` | **必填。** AGS 控制台创建的 SWE 沙箱工具 ID |
| `image` | `str` | `""` | SWE-bench 镜像名称，创建实例时覆盖默认镜像 |
| `secret_id` | `str` | `""` | 腾讯云 SecretId（或环境变量 `TENCENTCLOUD_SECRET_ID`） |
| `secret_key` | `str` | `""` | 腾讯云 SecretKey（或环境变量 `TENCENTCLOUD_SECRET_KEY`） |
| `region` | `str` | `"ap-chongqing"` | AGS 服务地域 |
| `domain` | `str` | `""` | 数据面网关域名（为空时根据 region 自动生成，如 `{region}.tencentags.com`） |
| `http_endpoint` | `str` | `"ags.tencentcloudapi.com"` | 控制面 API 端点 |
| `port` | `int` | `8000` | 沙箱内 SWE-ReX Server 端口 |
| `timeout` | `str` | `"1h"` | 沙箱实例存活时间（如 `"5m"`、`"1h"`） |
| `startup_timeout` | `float` | `180.0` | 等待运行时启动的超时（秒） |
| `runtime_timeout` | `float` | `60.0` | 运行时请求超时（秒） |
| `skip_ssl_verify` | `bool` | `False` | 是否跳过 SSL 证书验证 |

### AGSRuntimeConfig

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `ags_token` | `str` | `""` | AGS 网关访问 Token（`X-Access-Token` 头） |
| `auth_token` | `str` | `""` | SWE-ReX Server 认证 Token（`X-API-Key` 头） |
| `host` | `str` | `"https://127.0.0.1"` | 沙箱数据面 URL |
| `port` | `int\|None` | `None` | 端口（通常为 None，端口已嵌入 URL） |
| `timeout` | `float` | `60.0` | 请求超时（秒） |
| `skip_ssl_verify` | `bool` | `False` | 是否跳过 SSL 验证 |

## 架构

```
控制面 (ags.tencentcloudapi.com)          数据面 ({port}-{instanceId}.{domain})
┌─────────────────────────────┐          ┌──────────────────────────────────┐
│  DescribeSandboxToolList    │          │  GET  /is_alive                  │
│  StartSandboxInstance       │          │  POST /execute                   │
│  AcquireSandboxInstanceToken│  ──→     │  POST /create_session            │
│  DescribeSandboxInstanceList│  Token   │  POST /run_in_session            │
│  StopSandboxInstance        │          │  POST /read_file, /write_file    │
└─────────────────────────────┘          └──────────────────────────────────┘
      TencentAGSDeployment                         AGSRuntime
```

**控制面**：通过腾讯云 SDK 管理沙箱工具和实例，获取访问 Token。

**数据面**：通过 HTTP + `X-Access-Token` 头访问沙箱内的 SWE-ReX Server，执行命令、读写文件等。

## Token 管理

- Token 通过控制面 `AcquireSandboxInstanceToken` 接口获取，带有过期时间
- `AGSRuntime` 在每次请求前自动检查 Token 是否即将过期（< 60 秒）
- 过期时通过 `TencentAGSDeployment._ensure_valid_token` 回调自动刷新

## 并发安全

每个 `TencentAGSDeployment` 实例拥有独立的：
- 沙箱实例 ID
- Token 及其过期状态
- `AGSRuntime` 连接

多个实例可通过 `asyncio.gather()` 安全并发运行。

## 相关文件

| 文件 | 说明 |
|------|------|
| `src/swerex/deployment/ags.py` | `TencentAGSDeployment` 实现 |
| `src/swerex/runtime/ags.py` | `AGSRuntime` 实现 |
| `src/swerex/deployment/config.py` | `TencentAGSDeploymentConfig` 定义 |
| `src/swerex/runtime/config.py` | `AGSRuntimeConfig` 定义 |
| `tests/test_ags.py` | 单元测试（32 cases） |
