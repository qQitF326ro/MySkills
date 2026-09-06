# Windows、打包与编码

适用范围：脚本在 Windows 下运行、需要打包为 EXE、涉及中文文本或多进程时使用。

## Windows / multiprocessing

涉及 multiprocessing 或打包 EXE 时必须检查 `if __name__ == "__main__":`。Windows 使用 spawn 时注意：worker 可被子进程正常导入、不在模块 import 阶段创建进程、必要时 `multiprocessing.freeze_support()`、子进程数量受控、避免 EXE 递归启动自身、检查 PyInstaller 打包后的行为。

需同时验证 `.py` 与 `.exe` 在以下方面一致：脚本/EXE 所在目录、同目录 INI、日志、临时目录、subprocess、multiprocessing、第三方库资源。

## 路径兼容

使用 `sys.frozen` 判断运行环境：`.py` 用 `__file__` 所在目录，PyInstaller EXE 用 `sys.executable` 所在目录。读取脚本同级文件时以此目录为基准，不依赖当前工作目录。

## INI 配置

配置文件默认与脚本同名 `脚本名.ini`，使用 `configparser`，读写明确指定 `encoding="gb2312"`。配置不存在时根据需求创建并停止运行，配置项使用中文注释。

## 编码

文本读取优先 UTF-8，涉及旧 Windows/中文本地文件时考虑 GB2312/GBK。读取失败时是否 fallback 应根据数据来源决定。不要用 `errors="ignore"` 掩盖未知编码问题，可能静默丢数据。写文件必须明确指定 encoding。
