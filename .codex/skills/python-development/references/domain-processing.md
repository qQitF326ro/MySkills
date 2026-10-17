# 领域处理、依赖与性能

适用范围：图像处理、需要引入第三方库、判断性能优化时使用。PDF 与 OCR 专项见 `references/pdf-ocr.md`。

## 图像处理

使用 Pillow 等处理图像时检查：尺寸、DPI、色彩模式、位深、压缩方式、元数据、alpha、文件格式、内存占用。批量处理时再判断文件数量、单张大小、CPU 计算量、磁盘吞吐、输入/输出是否位于不同磁盘、网络、内存峰值，然后选择顺序、ThreadPoolExecutor、ProcessPoolExecutor。不要默认 Pillow 用线程更快。大量图片并发时注意 `worker 数 × 单任务内存`，避免同时打开过多大图，处理完及时释放 Image 与文件资源。

## 第三方依赖

标准库优先。只有当第三方库能明显提升功能、稳定性、性能、可维护性时才引入，例如 Pillow、PyMuPDF、pikepdf、img2pdf、openpyxl、pandas。引入时考虑实际用途、版本兼容、是否固定版本、PyInstaller 兼容性、EXE 额外处理。不要为简单功能引入过多依赖。

依赖 PyInstaller 支持情况是硬约束，打包前先确认：

```text
pikepdf / onnxruntime / rapidocr 等带数据文件或二进制扩展的包
需要用 --collect-all 或 --add-data 显式携带资源
```

细节见 `references/pyinstaller.md`。

## 性能

性能优化遵循测量、定位瓶颈、优化、验证。重点判断 CPU、RAM、disk read/write、network、filesystem metadata、external process、serialization。不要默认多线程、多进程、asyncio、batch 一定更快。

- CPU 已饱和时加线程通常无效；
- 磁盘到吞吐上限时并发收益低；
- 网络到带宽上限时线程不能突破；
- 大量小文件适度并发可能有效；
- 大量 CPU 密集任务可考虑多进程，但要计入序列化成本；
- 图像/PDF 批处理先测单文件耗时，再决定并发数与进程数，用真实数据验证，而不是凭经验拍数字。

没有测量条件时明确说明是估计，不要伪造 benchmark 结果。

## 验证产出

批量处理的验证不只是「程序没报错」，至少包含：

- 输入输出数量对应（处理 N 个产生 N 个，或明确说明差异原因）；
- 输出文件可重新打开，且关键属性正确（尺寸、页数、格式等）；
- 没有 0 字节或明显不完整的输出；
- 失败项进入失败清单 `failed_YYYYmmdd_HHMMSS.csv`（见 `references/resume-and-cli.md`）。

验证脚本或校验结果本身也应留下可复现产物（例如校验明细 CSV），而不是只在屏幕上打印一次。
