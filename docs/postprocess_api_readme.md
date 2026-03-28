# Preset Postprocess API 模块

基于 `ttd_fastapi_utils.preset` 的音频后处理独立 API 模块。

本文档描述的是当前仓库里的实际实现行为。

当前已适配 `ttd_fastapi_utils>=0.3.2`。

- 从 `0.3.1` 起，preset 本身不再内置“标准后处理链”参数；API 包装层会在 `use_standard_chain=true` 时先执行标准链，再应用 preset 效果，以保持外部调用体验稳定
- `0.3.2` 新增了 preset 元数据接口；`/postprocess/presets` 现在会把上游 metadata 一并返回

如果要实现上游调试页或正式 UI，先看 [postprocess_ui_split_guide.md](/workspace/svc-api/docs/postprocess_ui_split_guide.md)。

UI 侧需要特别注意：

- `Standard Postprocess` 必须有单独总开关
- 当总开关关闭时，`lufs` / `target_loudness`、`trim_silence`、`enable_eq` 应视为不生效

补充：

- `postprocess` 路由使用 `target_loudness` 作为响度参数名
- `postprocess/apply-generic` 同时支持 `lufs`，内部会映射到 `target_loudness`
- 主 VC 路由 `/infer_vc`、`/svc_file` 使用 `post_process` 作为标准链总开关
- 只有在 `post_process=true` 时，`lufs`、`trim_silence`、`enable_eq` 才参与计算

## 快速开始

### 挂载到 FastAPI 应用

```python
from fastapi import FastAPI
from postprocess_api import router as postprocess_router

app = FastAPI()
app.include_router(postprocess_router, prefix="/postprocess", tags=["postprocess"])
```

### 启动服务

当前入口是：

```bash
python api2.py
```

访问 Swagger UI：`http://localhost:7856/docs`

注意：

- 运行 `api2.py` 还依赖主服务本身的运行环境，不只是 `postprocess_api.py`
- 当前工作区里未看到 `.venv` / `uv` 配置，且本 shell 下 `conda` 不在 `PATH` 中；如果你本机用 conda，请先手动激活对应环境再启动

## API 列表

### 1. 获取可用 Presets

```http
GET /postprocess/presets
```

响应示例：

```json
{
  "presets": ["telephone", "smart_assistant", "inner_monologue", "radio", "intercom"],
  "aliases": {
    "telephone": ["phone", "电话"],
    "smart_assistant": ["assistant", "智能语音"],
    "inner_monologue": ["inner_voice", "心声独白", "心声"],
    "radio": ["广播"],
    "intercom": ["对讲机"]
  },
  "descriptions": {
    "telephone": "bandpass + saturation",
    "smart_assistant": "bandpass + light saturation + delay tail"
  },
  "metadata": {
    "telephone": {
      "display_name": "telephone 电话音",
      "summary": "bandpass + saturation",
      "primary_controls": ["low_cut_hz", "high_cut_hz", "wet_ratio", "drive", "limiter_threshold"],
      "recommended_standard_postprocess": {
        "enable": false,
        "target_loudness": -23.0,
        "trim_silence": false,
        "enable_eq": true
      }
    }
  }
}
```

### 2. 通用 Preset 应用

```http
POST /postprocess/apply-generic/{preset_name}
```

路径参数：

- `preset_name`: `telephone` | `smart_assistant` | `inner_monologue` | `radio` | `intercom`

表单参数：

- `file`: 文件上传字段，使用 `multipart/form-data`
- `target_loudness`: 默认 `-23.0`，范围 `-40 ~ -10`
- `trim_silence`: 默认 `false`
- `enable_eq`: 默认 `true`
- `enable_limiter`: 默认 `true`
- `limiter_threshold`: 默认 `0.98`
- preset 特有字段也统一通过表单字段提交

当前实现说明：

- 通用端点和专用端点都统一使用 Form 体验
- 通用端点为了兼容不同 preset，会暴露一组并集字段
- 与当前 preset 无关的字段会被忽略，不会透传到目标 preset

### 3. 专用端点

当前代码中声明了以下专用路由：

| 端点 | 描述 |
| ------ | ------ |
| `POST /postprocess/apply/telephone` | 电话音 |
| `POST /postprocess/apply/smart_assistant` | 智能语音 |
| `POST /postprocess/apply/inner_monologue` | 心声独白 |
| `POST /postprocess/apply/radio` | 收音机/广播 |
| `POST /postprocess/apply/intercom` | 对讲机 |

当前实现说明：

- 通用路由已改为独立路径 `POST /postprocess/apply-generic/{preset_name}`，避免与专用路由冲突
- 交互式调参优先使用专用端点
- 脚本化批量调用可以继续使用通用端点

## 使用示例

### cURL 示例

基础调用：

```bash
curl -X POST "http://localhost:7856/postprocess/apply/telephone" \
  -F "file=@input.wav" \
  -o output_telephone.wav
```

带公共参数的调用：

```bash
curl -X POST "http://localhost:7856/postprocess/apply/smart_assistant" \
  -F "file=@input.wav" \
  -F "target_loudness=-26.0" \
  -F "trim_silence=true" \
  -o output.wav
```

动态端点 + preset 特有参数：

```bash
curl -X POST "http://localhost:7856/postprocess/apply-generic/inner_monologue" \
  -F "file=@input.wav" \
  -F "lowpass_hz=2000" \
  -F "reverb_wet=0.4" \
  -F "wet_ratio=0.5" \
  -o output.wav
```

### Python 示例

```python
import requests

url = "http://localhost:7856/postprocess/apply/telephone"

with open("input.wav", "rb") as f:
    files = {"file": ("input.wav", f, "audio/wav")}
    data = {
        "target_loudness": -26.0,
        "trim_silence": "true",
    }
    response = requests.post(url, files=files, data=data)

with open("output.wav", "wb") as f:
    f.write(response.content)
```

## 参数说明

### 通用参数模型

以下参数来自 `PresetBaseParams`，但并不是所有端点都会以同一种方式暴露：

| 参数 | 类型 | 默认值 | 范围 | 说明 |
| ------ | ------ | -------- | ------ | ------ |
| `target_loudness` | float | -23.0 | -40 ~ -10 | 目标 LUFS 响度 |
| `trim_silence` | bool | false | - | 裁剪首尾静音 |
| `enable_eq` | bool | true | - | 启用 EQ；部分 preset 会覆盖默认值 |
| `enable_limiter` | bool | true | - | 启用限幅器 |
| `limiter_threshold` | float | 0.98 | 0.5 ~ 1.0 | 限幅器阈值；部分 preset 会覆盖默认值 |

### 各 Preset 参数模型

当前代码中仍然定义了以下参数模型，用于表达 preset 参数结构：

- `TelephoneParams`
- `SmartAssistantParams`
- `InnerMonologueParams`
- `RadioParams`
- `IntercomParams`

这些模型包含文档中列出的 preset 特有参数，例如：

- `telephone`: `use_standard_chain`、`enable_saturate`、`low_cut_hz`、`high_cut_hz`、`drive`
- `smart_assistant`: `enable_delay`、`delay_ms`、`decay`、`repeats`、`delay_wet`
- `inner_monologue`: `lowpass_hz`、`reverb_*`
- `radio`: `low_cut_hz`、`high_cut_hz`、`reverb_*`
- `intercom`: `low_cut_hz`、`high_cut_hz`、`reverb_*`

专用端点会把对应 preset 的字段直接暴露到 Swagger 表单中；通用端点则暴露一组并集字段，按所选 preset 过滤后再应用。

和 `0.3.1` 的对应关系：

- `use_standard_chain`、`target_loudness`、`trim_silence`、`enable_eq` 现在由 API 包装层先处理
- 真正传给 `ttd_fastapi_utils.preset` 的，只保留当前 preset 函数还接受的参数

## 错误处理

当前实现中，通用端点与专用端点统一具备以下错误处理：

| 阶段 | 响应 |
| ------ | ------ |
| 文件类型不是音频 | `400 Invalid file type` |
| 音频读取失败 | `400 Failed to load audio` |
| `apply_preset()` 抛出 `ValueError` | `400` |
| 其他处理异常 | `500 Audio processing failed` |

## Preset 效果速查

| Preset | 适用场景 | 核心特征 |
| ------ | ---------- | ---------- |
| **telephone** | 电话录音、IVR | 窄带 300-3400Hz，轻饱和 |
| **smart_assistant** | AI 助手语音 | 标准链 + 带通 + 空间尾音 |
| **inner_monologue** | 内心独白、冥想 | 柔和低通 + 回声 + 空气感 |
| **radio** | 广播、播客 | 中频突出 + 箱体感 |
| **intercom** | 对讲机、步话机 | 更窄更硬的中频 |

## 依赖

如果只看当前模块与入口文件，至少涉及：

```text
ttd_fastapi_utils>=0.3.2
fastapi
pydantic
soundfile
numpy
uvicorn
```

如果通过 `api2.py` 启动，还依赖主项目现有的 Seed-VC 运行环境。

## 文件说明

- `postprocess_api.py`：后处理路由模块与参数模型
- `api2.py`：主服务入口，挂载 postprocess 路由
- `docs/postprocess_api_design.md`：设计与实现说明
- `docs/postprocess_ui_split_guide.md`：给上游 UI / AI 实现者的职责拆分说明
