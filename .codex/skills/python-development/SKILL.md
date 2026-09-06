---
name: python-development
description: 新建、编写、修改、调试或优化 Python 脚本/程序时使用。提供复杂度判断、任务类型、并发/重试/超时、Windows/PyInstaller、数据完整性与验证等 Python 特有决策入口；通用 Agent 行为遵循 AGENTS.md。
---

# Python 开发规范

本 Skill 用于可交付的 Python 独立脚本、批处理工具和可打包 EXE 的程序。它不是 Python 教程，也不重复通用 Agent 行为；核心是提供「什么时候做什么、做到什么程度」的 Python 特有决策入口。

通用行为（最小必要修改、先读后改、文件系统安全、通用验证流程、任务复杂度、输出与自检）遵循全局 `AGENTS.md`，本技能不再复述。

## 三条最高原则

1. **不默认启用。** 不默认并发、重试、复杂架构或第三方库；方案规模与任务规模、风险、实际瓶颈匹配。
2. **数据安全优先。** 涉及删除、覆盖、移动、批量重命名、数据转换时，无法确认操作对象、范围、路径、格式或用途，不擅自扩大范围，必要时停止并说明缺失信息（详见 `AGENTS.md` 文件系统安全与 `references/data-integrity.md`）。
3. **验证结果而非「没报错」。** 验证的是输出正确性与数据完整性，不是程序是否正常退出。

## 任务类型入口

- **新建**：从零创建。基于 `assets/template.py` 复用公共结构，不要从空文件重写模板已有能力（见 `references/standalone-script.md`）。
- **修改**：先阅读相关代码、配置与调用，尽量局部修改，不无理由重写整个文件、换库、换配置/日志/输出格式；修改后验证新增与受影响的原有功能。
- **修复 Bug**：复现/收集错误、定位根因、最小修复、验证；不要通过大量修改「碰运气」。
- **优化**：测量、定位瓶颈、优化、验证；无证据表明存在性能问题时，不要为「优化」增加复杂并发或重构。

## 按需读取

| 场景 | 读取 |
| --- | --- |
| 判断是否并发、重试、超时、调用外部程序 | `references/concurrency-retry.md` |
| Windows / 打包 EXE / 路径 / INI / 编码 | `references/windows-packaging.md` |
| 覆盖、临时文件、防重复、资源、恢复、校验 | `references/data-integrity.md` |
| 图像 / PDF / 第三方依赖 / 性能 / 领域验证 | `references/domain-processing.md` |
| 新建独立脚本、复用模板 | `references/standalone-script.md` |

## 模板与验证

新建脚本前先读取 `assets/template.py`，确认其已给出的路径、配置、日志、retry、线程池、顶层异常处理是否满足需求，再决定复用或调整。

验证程度按任务风险决定（文件批处理、并发、multiprocessing、图像/PDF 各有针对性检查，见 `references/domain-processing.md`）；通用验证命令与失败处理流程遵循 `AGENTS.md`。
