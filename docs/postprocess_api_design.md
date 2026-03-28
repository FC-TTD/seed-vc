# Preset 后处理 API 设计与实现文档

## 1. 文档范围

本文档以当前仓库中的实际实现为准，说明已经落地的结构、接口行为和当前取舍。

当前实现已适配 `ttd_fastapi_utils>=0.3.2`。

## 2. 设计目标

为 Seed-VC API 提供独立的音频后处理端点，基于 `ttd_fastapi_utils.preset` 模块的 5 种场景预设，让用户可以对任意音频文件应用后处理效果。

## 3. 当前架构

### 3.1 模块拆分

```text
api2.py
  └── postprocess_api.py
        └── ttd_fastapi_utils.preset
              ├── telephone
              ├── smart_assistant
              ├── inner_monologue
              ├── radio
              └── intercom
```

### 3.2 路由挂载

```python
from postprocess_api import router as postprocess_router

app.include_router(postprocess_router, prefix="/postprocess", tags=["postprocess"])
```

挂载后对外路径为 `/postprocess/*`。

## 4. 路由设计与当前行为

### 4.1 已声明端点

| 类型 | 路径 | 说明 |
|------|------|------|
| 元数据 | `GET /postprocess/presets` | 返回 preset 名称、别名、描述、metadata |
| 通用 | `POST /postprocess/apply-generic/{preset_name}` | 统一入口 |
| 专用 | `POST /postprocess/apply/telephone` 等 | 代码中已声明 |

### 4.2 当前可稳定依赖的入口

当前已将专用路由放在动态路由之前注册，因此：

- `/postprocess/apply/telephone` 等专用端点可正常命中
- `/postprocess/apply-generic/{preset_name}` 继续作为统一入口保留
- 形成“专用端点负责更好调试体验，通用端点负责脚本化调用”的双层结构

## 5. 参数设计

### 5.1 参数模型

代码中定义了以下模型：

```text
PresetBaseParams
  ├── target_loudness
  ├── trim_silence
  ├── enable_eq
  ├── enable_limiter
  └── limiter_threshold

TelephoneParams
SmartAssistantParams
InnerMonologueParams
RadioParams
IntercomParams
```

这些模型在代码层面仍然有价值：

- 约束 preset 参数范围
- 描述参数结构
- 为后续真正暴露专用端点提供基础

其中有一层额外兼容语义：

- `use_standard_chain`
- `target_loudness`
- `trim_silence`
- `enable_eq`

这些字段在 `0.3.1` 起不再是 preset 库函数自身的参数，而是由我们的 API 包装层先消费，再决定是否调用 `postprocess.apply_postprocess()`。

`0.3.2` 进一步新增了 `preset_metadata()`，因此 `/presets` 现在会把上游 metadata 一并返回，避免 preset 摘要、推荐控制项和推荐标准链信息继续手写漂移。

### 5.2 当前实际暴露方式

当前端点统一采用 multipart form-data，参数暴露方式如下：

| 参数 | 位置 | 说明 |
|------|------|------|
| `preset_name` | path | preset 名称 |
| `file` | multipart form-data | 上传音频文件 |
| `target_loudness` | form | 默认 `-23.0` |
| `trim_silence` | form | 默认 `false` |
| `enable_eq` | form | 默认 `true` |
| `enable_limiter` | form | 默认 `true` |
| `limiter_threshold` | form | 默认 `0.98` |

补充说明：

- 专用端点通过 `Depends(Model.as_form)` 暴露对应 preset 的完整表单字段
- 通用端点暴露所有 preset 的并集字段，再按 `preset_name` 过滤到目标参数模型
- 不再使用 `params_json`
- 调用 preset 前，包装层会根据当前库函数签名过滤掉不再支持的参数
- `/presets` 会透传 `preset_metadata()` 的结果

## 6. 请求处理流程

### 6.1 通用端点

```text
UploadFile
  -> 文件类型检查
  -> _load_audio()
  -> 收集表单字段
  -> 根据 preset_name 过滤字段
  -> 实例化目标参数模型
  -> 如有需要先执行标准链
  -> apply_preset()
  -> _save_audio()
  -> StreamingResponse(audio/wav)
```

### 6.2 专用端点

专用端点的处理逻辑更直接：

```text
UploadFile
  -> _load_audio()
  -> preset_fn(..., **params.model_dump())
  -> _save_audio()
  -> StreamingResponse(audio/wav)
```

专用端点和通用端点最终都会进入统一的 `_process_preset_request()` 处理链。

## 7. 错误处理边界

### 7.1 通用端点已实现的处理

| 阶段 | 行为 |
|------|------|
| 文件类型检查失败 | 返回 400 |
| `soundfile` 读取失败 | 返回 400 |
| `apply_preset()` 抛出 `ValueError` | 返回 400 |
| 其他异常 | 返回 500，并记录日志 |

### 7.2 当前未统一的部分

当前专用端点与通用端点已经统一复用：

- 文件类型校验
- 音频读取异常包装
- preset 调用异常映射
- WAV 响应封装

另外，针对 `ttd_fastapi_utils 0.3.1` 的 preset 签名调整，包装层新增了“按当前函数签名过滤 kwargs”的保护，避免因为上游删参而把旧字段直接透传成 `TypeError`。

针对 `0.3.2`：

- `delay()` 的语义改成返回包含原始信号的 composite signal
- 新增 `delay_tail()` 作为 wet-only echo tail
- 新增 `preset_metadata()`

由于 wrapper 自己并不复刻 delay 链路，而是委托给上游 preset 实现，所以这里不需要手动改效果链，只需要升级包版本，并把 metadata 接进 API 返回。

## 8. 与 VC 流程的关系

`api2.py` 中现有 VC 端点仍然沿用 `apply_postprocess()` 做基础后处理，不直接走这里的 preset API。

也就是说当前关系是：

- `postprocess_api.py` 是独立路由能力
- `/infer_vc`、`/svc_file` 仍使用主流程里的基础后处理
- VC 路由正式暴露 `lufs` 作为响度参数，并兼容 `loudnorm` 旧别名
- `postprocess/apply-generic` 兼容 `lufs` 作为 `target_loudness` 的别名
- 两者目前并未在 API 层打通成统一配置入口

## 9. 性能与实现取舍

### 9.1 当前实现优点

- 路由模块与 VC 主业务解耦
- 音频读写走内存缓冲，不落盘
- 处理结果统一返回 WAV 流

### 9.2 当前实现限制

- 通用端点为了覆盖全部 preset，会暴露较大的字段并集
- 通用端点无法像专用端点那样只展示当前 preset 相关字段
- 这是一种“统一调用入口”和“表单清晰度”之间的取舍

## 10. 后续收敛方向

如果后续要把这套接口做成真正稳定的“通用 + 专用”双层 API，建议至少完成以下收敛：

1. 如果后续还要继续精简通用端点，可考虑拆出更轻量的“仅公共参数”入口。
2. 可以把重复的 `as_form()` 定义进一步抽象，减少模型维护成本。
3. 如果未来要和 VC 主流程打通，再考虑把 preset 作为统一后处理配置来源。
