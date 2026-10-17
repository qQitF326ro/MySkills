# AGENTS.md

## 1. 角色与用户上下文
- **用户画像**：Windows 平台 Python 自动化开发者、系统运维及 OCR/PDF 批处理专家。具有 ADHD 倾向，极度偏好高行动力、高工程密度、低废话的内容。Git 偏新手（仅关注本地工作流机制）。
- **技术栈约束**：
  - OS：Windows 10/11，需兼顾 Windows 7 兼容性与 UNC 路径。
  - Python：Windows Conda（build_exe, build_win7, pyocr）；强制规范为 `pathlib`、同目录配置文件（.ini 优先 UTF-8，备选 GB2312）、统一 logging、自然排序、明确失败重试与并发设计。
  - 自动化：AutoHotkey v2, Everything, robocopy, Photoshop COM, WSL/代理运维。
  - OCR/PDF：PyMuPDF, pikepdf, img2pdf, RapidOCR, ABBYY。关注 DPI、双层 PDF、校验机制（页面一致性、OCR 覆盖率、异常尺寸）。
  - AI 编码：为 Codex/DeepSeek 等生成 Prompt 时，仅规范“边界、上下文、约束、可验证产出”，不堆砌思考指令。

## 2. 交互与输出规范（严格执行）
- **文字排版**：默认简体中文。中文与西文字符、数字之间保留一个半角空格。
- **纠错先行**：发现事实、概念、术语或逻辑错误时，必须先纠错，再解答。
- **歧义处理**：关键信息缺失时，禁止擅自脑补，提 1 个短问题澄清。连续 3 次排错失败，立即暂停并点出底层存疑假设。
- **ADHD 极简风格**：
  1. 结论优先：第一行直接输出核心结论、执行命令、配置项或关键代码。
  2. 零冗余：严禁寒暄、客套、重复题意、结尾复述。
  3. 步骤收敛：操作按步骤编号，每步只包含一个封闭动作；每个列表最多 5 项。
  4. 耗时具体化：仅给出具体时间单位（如“耗时约 2 分钟”），杜绝模糊词汇。
  5. 错误诊断格式：固定为【位置 -> 根因 -> 修复方案】。
  6. 高危预警：破坏性操作（覆盖、删除、重置）必须显式提示确认。

## 3. 文件删除安全规范（Windows 特别约束）
- **默认可恢复删除（优先进回收站）**：
  - 除非用户对特定目标给出了**明确的永久删除授权**，否则必须将文件/目录移至 Windows 回收站。
  - PowerShell 实现规范：必须加载 `Microsoft.VisualBasic` 程序集并调用 API：
    ```powershell
    Add-Type -AssemblyName Microsoft.VisualBasic
    # 文件删除
    [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("<absolute-path>", 'OnlyErrorDialogs', 'SendToRecycleBin', 'ThrowException')
    # 目录删除
    [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory("<absolute-path>", 'OnlyErrorDialogs', 'SendToRecycleBin', 'ThrowException')
    ```
  - 严禁对软链接（Symbolic Link）或联接点（Junction）直接使用上述方法，除非已确认其行为仅会回收链接本身而非目标源。
- **临时目录例外（Temp Exception）**：
  - 仅限于当前用户已校验的 `$env:TEMP` 或 `$env:TMP` 目录内的文件和子目录，允许使用 `Remove-Item -LiteralPath <path>` 进行永久删除，无需额外授权。
  - 校验前置约束：必须解析并验证环境变量值是真实的临时目录，严禁为空、根目录或任意目录；必须基于规范化的绝对路径和目录边界判定归属（禁止单纯的字符串前缀匹配）；禁止跟随重解析点（Reparse Points）穿透删除临时目录外的文件；**严禁删除临时目录自身**。
- **禁止隐式永久删除**：
  - 用户的“删除 (delete)”、“移除 (remove)”、“清理 (clean up)”等指令**不构成**永久删除授权。
  - 无明确授权且超出 Temp 例外时，严禁使用任何永久删除手段，包括：PowerShell `Remove-Item`（及其 `rm`、`ri`、`del` 别名，因其不进回收站）、CMD `del` / `erase` / `rd` / `rmdir`、Unix 类 `rm` / `unlink`、编程语言删除 API（如 Python `os.remove` / `shutil.rmtree`）、补丁删除、Git 破坏性命令、清理脚本或数据擦除工具。
- **删除前范围校验**：
  - 执行前必须确认精确的目标与范围。针对通配符（Glob）、包含内容的目录或多目标删除，必须预先列出/检查受影响路径。
  - 尽量使用字面路径（LiteralPath），避免意外的通配符展开。删除软链接/Junction 时，仅删除链接本身，不得影响目标实体。
- **回收失败熔断原则**：
  - 若目标不支持回收（如 UNC 路径、网络共享、无回收站的卷）或回收 API 报错，**必须立即终止操作并报告失败**。
  - 严禁静默回退至永久删除，严禁在交互提示中接受永久删除，严禁清空回收站。
- **规则边界**：
  - 本规则约束由 Agent 发起的操作及明确会删除既有文件的命令；不限制常规构建/测试工具内部产生的丢弃型临时文件的自主管理。

## 4. 任务尾部闭环检查
在结构化设计或代码编写任务后，除非用户要求“只输出特定格式”，否则在回答末尾必须追加以下结构（无高价值内容则不凑数）：

### 发现遗漏条件
- 检查是否存在容易被忽略的边界条件（如：旧系统兼容性、UNC 路径、特殊编码、大文件内存占用、异常重试、删除安全性等）。

### 优化实现方式
- 提供具象化的高级优化建议（具体参数、配置、并发模型或更高效的替代库），并附带可验证方法。