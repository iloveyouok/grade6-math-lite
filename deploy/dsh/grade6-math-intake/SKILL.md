---
name: grade6-math-intake
description: 结合腾讯云 OCR 与 grade6-math-lite MCP 工具批量处理成绩单、筛选导入临界生、批量归类作业错题；严格执行“本地脱敏 + 先预览确认再入库”红线。
whenToUse: 当教师在对话中上传成绩单图片、Excel/CSV 名单、或作业照片压缩包，要求筛选临界生导入、批改错题或分析学情并写入六年级数学工作台时使用。
---

# 六年级数学成绩与作业批处理助手（grade6-math-intake）

## 0. 安全与隐私红线（必须严格遵守）

1. **绝不向上下文或外部模型暴露学生真实姓名**：后端返回的所有数据已脱敏为 `anonymous_code`（如 `g6_c01_s08`）或临时编号（`cand_001`）+ `student_no`（座号）。推理、总结和预览一律只使用“座号 + 匿名编号”。
2. **禁止绕过 MCP 工具直接改数据库**：所有写入操作必须通过 `mcp__math__math_preview_import_plan` 与 `mcp__math__math_commit_import_batch` 完成。
3. **禁止静默写库**：必须先向教师展示清晰的预览表格（拟新建/更新的学生、分数异常提示、筛选理由、错题归因母题）并等待教师明确回复“确认/导入”后，才允许以 `confirmed_by_user: true` 调用 `mcp__math__math_commit_import_batch`。

## 1. 接收附件并触发腾讯 OCR（确定性识图）

不要用你自己的视觉能力去猜成绩单图片上的数字。当教师上传了附件时，使用专用上传桥接命令把会话附件路径传给业务服务器完成腾讯 OCR 与本地脱敏：

- 成绩单照片 / Excel / CSV：
  ```bash
  python3 /workspace/bin/upload_batch_to_math.py --type dsh_grade_sheet --title "<测验标题>" "<附件路径1>" ["<附件路径2>"...]
  ```
- 学生作业图片 / ZIP 压缩包：
  ```bash
  python3 /workspace/bin/upload_batch_to_math.py --type homework_batch --title "<作业批次标题>" "<附件路径>"
  ```

如教师未附带附件，先询问重新上传。

## 2. 清洗、对齐与规则筛选

拿到 `batch_id` 与脱敏后的 `desensitized_rows` / `tasks` 后：

1. 调用 `mcp__math__math_list_students` 获取当前已追踪临界生基线分与现状。
2. 调用 `mcp__math__math_list_mothers` 获取标准母题 ID（`G6-CALC-01`、`G6-FRAC-01`、`G6-RATIO-01`、`G6-GEO-01`）。
3. 清洗 OCR 结果：
   - 合并跨页/跨图重复行；
   - 对非数字得分（如 `病假`、`缺考`）或明显识别异常（如 >100 或负分）设置 `score: null` 并在 `warning` 标注；
   - 按教师要求（默认 40–58 分或明显退步）判定 `recommended: true/false` 并说明 `reason`；
   - 如含错题明细，准确映射到对应的 `kb_mother_id` 与 `module_tag`。

## 3. 提交预览并等待教师确认

调用 `mcp__math__math_preview_import_plan` 提交结构化方案，拿到后端校验报告与警告信息后，向教师输出简洁预览表：

- 列出：`座号 | 编号 | 原估分 | 本次分数 | 动作(新建/更新) | 是否建议纳入 | 异常提醒`
- 说明可在本对话回复 **“确认导入”**，也可以在 `grade6-math-lite` 网页端「学生 → 筛选导入学生 → DSH 待确认批次」里对照真名直接点击确认。

## 4. 确认后落库

收到教师明确同意后，调用：
```json
mcp__math__math_commit_import_batch({ "batch_id": "<batch_id>", "confirmed_by_user": true })
```
并汇报实际新建、更新学生数及入库错题数。
