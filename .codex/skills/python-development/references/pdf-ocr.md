# PDF 与 OCR 处理

适用范围：使用 PyMuPDF、pikepdf、img2pdf、RapidOCR、ABBYY 等处理 PDF、图像转 PDF、双层 PDF、OCR 识别与校验时使用。通用文件安全规则见 `references/data-integrity.md`，失败清单见 `references/resume-and-cli.md`。

## 处理前必须探测的输入属性

打开文档后先探测，再决定流程；不要假设输入是「干净的标准 PDF」：

```text
页数、加密状态（doc.needs_pass）、是否已修复（doc.is_repaired）
每页尺寸与旋转（MediaBox / CropBox / rotation）
是否已有文字层（逐页 len(page.get_text().strip())）
每页图像数量与像素尺寸（page.get_images(full=True)）
是否有表单、附件、签名（签名文档重写会破坏签名）
```

任何一项不符合预期时，先报告再继续，不要静默「按最乐观情况处理」。

## 双层 PDF 与 OCR 校验

OCR 的产物是「图像 + 文字层」的双层 PDF，必须逐项校验，不能只看程序退出码：

| 校验项 | 合格标准 |
| --- | --- |
| 页数一致性 | 输出页数 == 输入页数 |
| 页面尺寸一致性 | 每页 MediaBox / CropBox 与输入一致（容差 0 或 ≤ 1 pt） |
| 旋转一致性 | rotation 与输入一致 |
| 文字层覆盖率 | 有文字的页数 / 总页数 == 100%（逐页判定，不取平均） |
| 文字层可用性 | 每页字符数不低于该页图像面积的合理下限；连续抽样「空层」即为失败 |
| 文件可用性 | 用 PyMuPDF 重新打开，且 `page.get_text()` 不抛异常 |
| 输出完整性 | 无 0 字节文件，体积与页数比值在合理区间 |

覆盖率校验示例（逐页判定，避免「大部分页正常」掩盖个别空白页）：

```python
import fitz  # PyMuPDF


def ocr_coverage(pdf_path, min_chars_per_page=10):
    """返回 (有文字页数, 总页数, 空页页码列表)。"""
    with fitz.open(pdf_path) as doc:
        empty_pages = []
        for index, page in enumerate(doc, start=1):
            if len(page.get_text().strip()) < min_chars_per_page:
                empty_pages.append(index)
        return doc.page_count - len(empty_pages), doc.page_count, empty_pages
```

校验结果落成可复查的产物，而不是只打印一行：

```text
ocr_check_YYYYmmdd_HHMMSS.csv
列：页码、字符数、页宽、页高、旋转、图像数、状态
```

失败页进入 `failed_*.csv`，由 `--only-failed` 二次处理。

## DPI 与图像层

- 输入扫描件先确认有效 DPI：用页面尺寸与图像像素尺寸换算（`像素宽 / 页面宽(英寸)`）；
- OCR 前把低 DPI 页重采样到目标 DPI（常见 300 DPI），过度重采样不会增加信息，只会增大体积与耗时；
- 输出双层 PDF 时保持原始图像像素不变，避免二次压缩导致画质下降；
- 记录每页 DPI，低于阈值时在日志中标注，属于需要人工复核的情况。

## img2pdf：无损的前提

- 仅当输入是 JPEG / JPEG2000 时才能真正无损嵌入；PNG 等格式会被转码；
- 不要为了「看起来更小」而降低质量参数，扫描件降质不可逆；
- 不要用 `--force` 或直接写回源文件覆盖输入；输出走临时文件 + 验证 + 替换流程；
- 按资源管理器顺序组装页序（见 `references/windows-packaging.md`），页码错序是这类任务最常见的翻车点。

## pikepdf：改结构时注意

- 线性化（`save(..., linearize=True)`）改善首屏加载，但会整体重写文件，属于耗时操作；
- 对象流压缩 `object_stream_mode` 可减小体积，但会降低下游工具兼容性，按需选择；
- **带数字签名的 PDF 不要重写**，重写会破坏签名有效性；
- 读出后再写回时，只改需要改的部分，不要「顺手清理」元数据、书签等用户数据。

## PyMuPDF：大文件内存

- 不要默认一次性 `doc.load_page(i)` 全部页面并保持引用；逐页处理并及时释放；
- 渲染位图 `get_pixmap()` 的内存 = 宽 × 高 × 通道数，A3/600 DPI 单页可达数百 MB，必须先算内存再决定并发；
- 写入必须用 `tmp` 文件 + 验证 + 替换，不要在源文件路径上原地写。

## OCR 引擎取舍

| 引擎 | 适用 | 注意 |
| --- | --- | --- |
| RapidOCR（PaddleOCR ONNX） | 离线、无授权成本、中文常规印刷体、可嵌入 EXE | 版式复杂/表格/竖排效果一般；每个进程都会加载一份模型，注意内存峰值 |
| ABBYY | 复杂版式、表格、多语言、精度优先 | 商业授权；通常通过命令行/脚本驱动，须检查 returncode 与输出目录 |

降级路径：RapidOCR 逐页低置信度（或空层）时标记该页 `需人工复核 / 待 ABBYY`，写入校验 CSV 与失败清单，而不是直接判定整份文件失败。

## 并发选型（OCR 是 CPU-bound）

- OCR、图像重采样、PDF 重压属 CPU-bound，使用 `ProcessPoolExecutor` / `multiprocessing`，不要用线程池幻想加速；
- 并发数起步建议「物理核心数 / 2」，再用真实样本实测调优；HT 核心收益有限；
- 内存峰值 ≈ `进程数 × (模型 + 单页位图)`，先估算再定进程数；进程数过大会触发换页反而更慢；
- `if __name__ == "__main__":` + `multiprocessing.freeze_support()` 必须写，打包后尤其容易踩（见 `references/windows-packaging.md`）；
- 输入在 UNC / 网络路径时，先落地到本地临时目录再处理，避免多进程争抢 SMB 句柄。

## 收尾验证清单

```text
1. 输出页数与输入页数一致
2. 每页尺寸、旋转与输入一致
3. 文字层覆盖率 100%，无连续空层
4. ocr_check_*.csv 已生成，异常页已标注
5. 失败页进入 failed_*.csv，可 --only-failed 重跑
6. 输出文件可重新打开，无 0 字节文件
7. 源文件未被覆盖；UNC 场景替换前已备份到脚本目录
```
