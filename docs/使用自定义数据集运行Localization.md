# 使用自定义数据集运行 Localization

## 1. 你的数据集是否满足要求？

对比 [agentless/multilang/utils.py:7-15](agentless/multilang/utils.py#L7-L15) 中的 `process()` 函数：

```python
def process(raw_data):
    raw = json.loads(raw_data)
    data = {
        'repo': f'{raw["org"]}/{raw["repo"]}',                      # e.g. "Open-Cascade-SAS/OCCT"
        'instance_id': raw['instance_id'],                          # e.g. "OCCT-1"
        'base_commit': raw['base']['sha'],                           # commit hash
        'problem_statement': raw['resolved_issues'][0]['title'] + '\n' + raw['resolved_issues'][0]['body'],
    }
    return data
```

**你的数据集 `Def4CAE_withOut_fenics.jsonl` 结构：**
```json
{
  "org": "Open-Cascade-SAS",
  "repo": "OCCT",
  "instance_id": "OCCT-1",
  "base": {"sha": "fe1382f3c25949e133e560976988ccb0e605cde1"},
  "resolved_issues": [{"title": "...", "body": "..."}]
}
```

**结论：你的数据集已经满足 Localization 的所有必要字段！** 项目会自动：
- 拼接 `org/repo` 为完整仓库名
- 从 `base.sha` 提取 `base_commit`
- 从 `resolved_issues[0]` 拼接 `problem_statement`

---

## 2. Localization 流程分析

根据 [agentless/fl/localize.py](agentless/fl/localize.py) 和 [agentless/fl/FL.py](agentless/fl/FL.py)，Localization 分两个阶段：

### 2.1 Stage 1: File-level Localization (文件定位)
```
输入: problem_statement + structure (项目结构)
输出: found_files (需要修改的文件列表)
LLM Prompt: "请阅读问题描述和项目结构，列出需要修改的文件（最多5个）"
```

### 2.2 Stage 2: Edit-level Localization (编辑位置定位)
```
输入: problem_statement + found_files 的代码内容
输出: found_edit_locs (具体行号/函数/类名)
LLM Prompt: "请阅读问题描述和相关文件，提供需要编辑的具体位置（行号/函数/类名）"
```

---

## 3. 输出结果格式

运行后会生成 `results/{FOLDER_NAME}/file_level/loc_outputs.jsonl`，每行包含：

```json
{
  "instance_id": "OCCT-1",
  "found_files": ["src/RWGltf/RWGltf_CafWriter.cxx"],
  "file_traj": {
    "prompt": "...",
    "response": "...",
    "usage": {"prompt_tokens": 1000, "completion_tokens": 200}
  },
  "found_related_locs": {},
  "related_loc_traj": [],
  "found_edit_locs": {
    "src/RWGltf/RWGltf_CafWriter.cxx": ["line: 1822"]
  },
  "edit_loc_traj": {
    "prompt": "...",
    "response": "...",
    "usage": {"prompt_tokens": 1500, "completion_tokens": 300}
  }
}
```

**Trajectory 已经自动记录** — 每个阶段的 `prompt`、`response`、`usage` 都会保存。

---

## 4. Trajectory 日志

除了 `loc_outputs.jsonl` 中的 trajectory，每个 instance 还会生成详细日志：
```
results/{FOLDER_NAME}/file_level/localization_logs/{instance_id}.log
```
包含更详细的调试信息。

---

## 5. API 配置

### 5.1 支持的 API 提供商

项目在 [agentless/util/model.py:376-409](agentless/util/model.py#L376-L409) 中支持三种后端：

| Backend | 环境变量 | 说明 |
|---------|----------|------|
| OpenAI | `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL` | 默认 |
| Anthropic | `ANTHROPIC_API_KEY` | Claude 系列 |
| DeepSeek | 使用 OpenAI 兼容接口 | `base_url="https://api.deepseek.com"` |

### 5.2 如何使用 MiniMax API

MiniMax 提供 OpenAI 兼容接口，有两种方式：

**方式一：使用 OpenAI 后端 + MiniMax base_url**
```bash
export OPENAI_API_KEY="your-minimax-api-key"
export OPENAI_BASE_URL="https://api.minimax.chat/v1"  # MiniMax OpenAI-compatible endpoint
export OPENAI_MODEL="your-model-name"
```

**方式二：参考 DeepSeek 添加专用后端**（[model.py:327-373](agentless/util/model.py#L327-L373)）
```python
class MiniMaxChatDecoder(DecoderBase):
    def codegen(self, message, num_samples=1, prompt_cache=False):
        ...
        ret = request_chatgpt_engine(
            config, self.logger, base_url="https://api.minimax.chat/v1"
        )
        ...
```

### 5.3 查看当前配置

在 `script/localization*.sh` 中可以看到：
```bash
python agentless/fl/localize.py \
    --file_level \
    --model $MODEL \      # 模型名称
    --backend $BACKEND    # openai / anthropic / deepseek
```

---

## 6. 运行前准备

1. **设置 API Key**
   ```bash
   export OPENAI_API_KEY="your-key"
   # 或
   export ANTHROPIC_API_KEY="your-key"
   ```

2. **确保仓库可访问** — `get_repo_structure()` 会尝试 clone 仓库到 `playground/` 目录

3. **确认数据集格式** — 你的 `Def4CAE_withOut_fenics.jsonl` 格式已对齐，可直接使用

4. **运行 Localization**
   ```bash
   # 修改 script/localization1.1.sh 中的 DATASET 为你的数据集路径
   ./script/localization1.1.sh
   ```

---

## 7. Ground Truth 对比

定位结果的 ground truth 通常是 `found_files` 和 `found_edit_locs`。你可以：

1. 从你的数据集补充 `ground_truth` 字段（可选，不影响定位运行）
2. 定位完成后，用 `loc_outputs.jsonl` 与你的 ground truth 对比评估
