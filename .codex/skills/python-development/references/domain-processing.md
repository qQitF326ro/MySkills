# 领域处理、依赖与性能

适用范围：图像、PDF 等具体格式处理，或需要引入第三方库、判断性能优化时使用。

## 图像处理

使用 Pillow 等处理图像时检查：尺寸、DPI、色彩模式、位深、压缩方式、元数据、alpha、文件格式、内存占用。批量处理时再判断文件数量、单张大小、CPU 计算量、磁盘吞吐、输入/输出是否位于不同磁盘、网络、内存峰值，然后选择顺序、ThreadPoolExecutor、ProcessPoolExecutor。不要默认 Pillow 用线程更快。大量图片并发时注意 `worker 数 × 单任务内存`，避免同时打开过多大图，处理完及时释放 Image 与文件资源。

## PDF 处理

使用 PyMuPDF、pikepdf、img2pdf 等处理 PDF 时检查：页数、文件大小、图像数量、内存占用、临时文件、输出完整性、是否覆盖源文件、DPI、MediaBox/CropBox、元数据、书签、文字/图像内容。大型 PDF 不要默认一次性全量加载内存。生成 PDF 后条件允许时重新打开或做针对性验证，确认文件可用。错误日志至少能定位具体文件、处理阶段、具体异常。

## 第三方依赖

标准库优先。只有当第三方库能明显提升功能、稳定性、性能、可维护性时才引入，例如 Pillow、PyMuPDF、pikepdf、openpyxl、pandas。引入时考虑实际用途、版本兼容、是否固定版本、PyInstaller 兼容性、EXE 额外处理。不要为简单功能引入过多依赖。

## 性能

性能优化遵循测量、定位瓶颈、优化、验证。重点判断 CPU、RAM、disk read/write、network、filesystem metadata、external process、serialization。不要默认多线程、多进程、asyncio、batch 一定更快。CPU 已饱和则加线程通常无效；磁盘到吞吐上限则并发收益低；网络到带宽上限则线程不能突破。大量小文件适度并发可能有效，大量 CPU 密集任务可考虑多进程，大量数据需要进程间传输必须考虑序列化成本。没有测量条件时明确说明是估计，不要伪造 benchmark 结果。
