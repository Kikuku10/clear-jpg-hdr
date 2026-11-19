# 清除JPG HDR小工具

在 macOS Finder 中右键选中 JPG、JPEG 或文件夹，选择「清除 JPG HDR 增益图」，即可移除明确标记的 HDR 增益图。主图无需重新压缩。文件夹只处理当前层，多选也可以使用。

## 设置

双击「清除JPG HDR小工具」打开设置。首次使用时，点击「管理扩展…」，在系统设置中启用同名 Finder 扩展。

| 设置 | 默认值 | 作用 |
| --- | --- | --- |
| 显示「清除 JPG HDR 增益图」 | 开 | 在图片或文件夹的 Finder 一级右键菜单中显示此操作 |
| 保留原图备份 | 关 | 开启后，只在即将处理照片时，在同一目录保存完整原图，如 `照片.jpg_original` |

备份关闭时直接修改原文件。备份开启时，如果已有同名备份或备份失败，会保留原图并报告失败。需要恢复时，可将备份复制到其他目录，再删去副本文件名末尾的 `_original`。

处理成功和跳过均静默，只有失败会提示。普通 JPEG、主图自身为 HDR、未明确标记增益图和包含其他附图的文件会跳过。当前支持 MPF 中恰有一张主图和一张明确标记的 Gain Map 的 JPEG。

## 从源码构建

需要 macOS 13 或更新版本、包含 Swift 编译器和 Python 3 的 Xcode 或 Command Line Tools，以及 ExifTool。使用 Homebrew 时可执行 `brew install exiftool`。运行时需要 `/usr/bin/python3` 可用。

在项目根目录运行：

```sh
/usr/bin/python3 finder-menu/build_app.py build
open build
```

将生成的「清除JPG HDR小工具.app」拖入「应用程序」，双击打开并启用 Finder 扩展。构建脚本为当前 Mac 架构生成本地签名的 App。重复构建时，请指定新的输出目录。

## 测试

```sh
/usr/bin/python3 -m unittest discover -s tests -v
```

测试对临时副本运行真实的 ExifTool，检查主图、EXIF、ICC、文件属性、备份以及异常处理。测试图片来自 libavif，来源和许可见 [tests/fixtures](tests/fixtures/)。
