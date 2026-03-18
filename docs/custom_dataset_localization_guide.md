# 自定义数据集运行 Localization 指南

## 概述

MagentLess 项目的定位（Localization）阶段用于识别需要修复的代码位置。该阶段**不需要**修复结果、测试结果等字段，仅需要能够定位问题的最小信息。

---

## 数据集字段要求

### 必要字段

| 字段 | 来源 | 说明 |
|------|------|------|
| `org` | 顶层 | GitHub 组织名 |
| `repo` | 顶层 | 仓库名 |
| `instance_id` | 顶层 | 实例唯一标识符，格式建议 `org__repo__number` |
| `base.sha` | `base` 字典 | 指定要检出的 commit SHA |
| `resolved_issues[0].title` | `resolved_issues` 列表第一个元素 | 问题标题 |
| `resolved_issues[0].body` | `resolved_issues` 列表第一个元素 | 问题详细描述 |

### 不需要的字段（定位阶段不使用）

以下字段在 Localization 阶段**完全不会被读取**，即使为空或缺失也不影响运行：

- `fixed_tests`
- `n2p_tests`
- `run_result`
- `test_patch_result`
- `fix_patch_result`
- `p2p_tests`
- `f2p_tests`
- `s2p_tests`
- `fix_patch`
- `test_patch`

---

## 数据格式示例

```json
{
  "org": "simdjson",
  "repo": "simdjson",
  "instance_id": "simdjson__simdjson__2178",
  "base": {
    "sha": "bf7834179c1f8fc523c9fd73d29b46348ae1d576"
  },
  "resolved_issues": [
    {
      "title": "Better error for JSON Pointer",
      "body": "The call `.at_pointer(\"/document/key4/sub\")` fails but the reported error is INVALID_JSON_POINTER..."
    }
  ]
}
```

> **注意**：`resolved_issues` 必须是列表，定位代码只读取第一个元素（`resolved_issues[0]`）。

---

## 内部数据处理流程

定位阶段的数据处理在 `get_repo_structure/get_repo_structure.py` 的 `process()` 函数中完成：

```python
def process(raw_data):
    raw = json.loads(raw_data)
    data = {
        'repo': f'{raw["org"]}/{raw["repo"]}',
        'instance_id': raw['instance_id'],
        'base_commit': raw['base']['sha'],
        'problem_statement': raw['resolved_issues'][0]['title'] + '\n' + raw['resolved_issues'][0]['body'],
    }
    return data
```

这 4 个字段是 Localization 的最小输入。

---

## 运行方式

### 定位流程的三个层级

| 层级 | 脚本 | 功能 |
|------|------|------|
| File Level | `localization1.1.sh` / `localization1.2.sh` | 找出需要修改的文件 |
| Related Level | `localization2.1.sh` | 进一步定位到具体的类/函数 |
| Fine-grain Line Level | `localization3.2.sh` | 精确到行号 |

### 最小运行命令（仅 File Level）

```bash
./script/localization1.2.sh
```

### 完整定位流程

```bash
./script/localization1.1.sh    # File level
./script/localization2.1.sh    # Related level
./script/localization3.2.sh    # Fine-grain line level
```

---

## 输出格式

### 输出结构

定位结果输出为 **JSONL 格式**（每行一个 JSON 对象），保存在 `results/{FOLDER_NAME}/{stage}/loc_outputs.jsonl`

### 输出层级与字段

定位流程三个层级的输出结构：

#### 1. File Level 输出（`found_files`）

```json
{
  "instance_id": "simdjson__simdjson__2178",
  "found_files": [
    "include/simdjson/dom/element-inl.h",
    "include/simdjson/error.h"
  ],
  "file_traj": {
    "prompt": "...",
    "response": "...",
    "usage": {"prompt_tokens": 1000, "completion_tokens": 200}
  }
}
```

#### 2. Related Level 输出（`found_related_locs`）

```json
{
  "instance_id": "simdjson__simdjson__2178",
  "found_files": ["include/simdjson/dom/element-inl.h"],
  "found_related_locs": {
    "include/simdjson/dom/element-inl.h": [
      "function: element::at_pointer",
      "function: is_pointer_well_formed"
    ]
  },
  "related_loc_traj": [
    {"prompt": "...", "response": "...", "usage": {...}}
  ]
}
```

#### 3. Fine-grain Line Level 输出（`found_edit_locs`）

```json
{
  "instance_id": "simdjson__simdjson__2178",
  "found_edit_locs": [
    {
      "include/simdjson/dom/element-inl.h": "line: 10\nline: 51",
      "include/simdjson/error.h": "line: 42"
    }
  ],
  "edit_loc_traj": {
    "prompt": "...",
    "response": ["line: 10\nline: 51", "line: 42"],
    "usage": {"prompt_tokens": 1500, "completion_tokens": 300}
  }
}
```

### 定位类型格式

定位代码（`extract_locs_for_files()`）使用以下格式描述定位结果：

| 类型 | 格式 | 说明 |
|------|------|------|
| 行号 | `line: N` | 第 N 行需要修改 |
| 函数 | `function: func_name` 或 `function: ClassName.method_name` | 特定函数/方法 |
| 类 | `class: ClassName` | 整个类 |
| 变量 | `variable: var_name` | 全局变量 |

### 输出字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `instance_id` | string | 实例唯一标识，与输入对应 |
| `found_files` | list[string] | 识别出的需要修改的文件路径列表 |
| `found_related_locs` | dict | 文件路径 → 定位类型列表的映射 |
| `found_edit_locs` | list[dict] | 每个 sample 的行级定位结果 |
| `*_traj` | dict | LLM 调用轨迹（prompt/response/usage） |

### Ground Truth 对比建议

你的 Ground Truth 应该对标以下层级：

1. **File Level**：文件路径列表 → 对比 `found_files`
2. **Related Level**：文件 + 函数/类 → 对比 `found_related_locs`
3. **Line Level**：文件 + 行号 → 对比 `found_edit_locs`

定位类型前缀 `line:`、`function:`、`class:`、`variable:` 需保持一致。

---

## API 配置

### 支持的 Backend

项目支持三种 API backend，通过 `--backend` 参数指定：

| backend | 说明 | API Key 环境变量 |
|---------|------|-----------------|
| `openai` | OpenAI API (GPT 系列) | `OPENAI_API_KEY` |
| `anthropic` | Anthropic API (Claude 系列) | `ANTHROPIC_API_KEY` |
| `deepseek` | DeepSeek API | `DEEPSEEK_API_KEY` |

### 环境变量配置

```bash
# OpenAI
export OPENAI_API_KEY="sk-..."
export OPENAI_MODEL="gpt-4o"           # 可选，默认 gpt-3.5-turbo
export OPENAI_BASE_URL=""               # 可选，留空为官方地址

# Anthropic
export ANTHROPIC_API_KEY="sk-ant-..."

# Deepseek
export DEEPSEEK_API_KEY="..."
```

### 切换到 MiniMax

MiniMax 提供 OpenAI-compatible API，理论上可以使用 `openai` backend，通过设置 `OPENAI_BASE_URL` 指向 MiniMax 端点：

```bash
export OPENAI_API_KEY="your-minimax-api-key"
export OPENAI_BASE_URL="https://api.minimax.chat/v1"   # MiniMax OpenAI-compatible endpoint
export OPENAI_MODEL="MiniMax-Text-01"                   # MiniMax 模型名
```

然后运行定位脚本时指定 `openai` backend：

```bash
./script/localization1.2.sh --backend openai --model $OPENAI_MODEL
```

> **注意**：需要确认 MiniMax 的 API 响应格式与 OpenAI Chat Completions API 完全兼容，特别是：
> - 响应结构 (`choices[0].message.content`)
> - `usage` 字段格式
> - 错误处理格式

---

## 注意事项

1. **仓库访问**：定位代码会根据 `base.sha` 克隆对应版本的仓库，确保网络能够访问 GitHub
2. **instance_id 格式**：虽然项目内部使用 `org__repo_PR-number` 格式，但只要保证唯一性即可
3. **problem_statement**：建议包含足够的上下文信息，这对 LLM 准确定位至关重要
