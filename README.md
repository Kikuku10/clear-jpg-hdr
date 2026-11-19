# 清除JPG HDR小工具

在 macOS Finder 中右键选中 JPG、JPEG 或文件夹，选择「清除 JPG HDR 增益图」，即可移除明确标记的 HDR 增益图。主图无需重新压缩。文件夹只处理当前层，多选也可以使用。

## 下载与安装

1. 在 [GitHub Releases](https://github.com/Kikuku10/clear-jpg-hdr/releases/latest) 下载适合自己 Mac 的 DMG。Apple 芯片选 `arm64`，Intel 选 `x86_64`。最低系统版本为 macOS 13。
2. 打开 DMG，将「清除JPG HDR小工具」拖到旁边的「Applications」文件夹。Python、Perl 和 ExifTool 均随 App 安装。
3. 从「应用程序」启动小工具，点击「管理扩展…」启用 Finder 扩展，然后右键选择照片或文件夹。

v1.2.0 使用本地签名，未经 Apple 公证。首次启动若被 Gatekeeper 拦截，可在核实下载来源后按 [Apple 的首次打开说明](https://support.apple.com/zh-cn/102445) 操作。

## 设置

双击「清除JPG HDR小工具」打开设置。

| 设置 | 默认值 | 作用 |
| --- | --- | --- |
| 显示「清除 JPG HDR 增益图」 | 开 | 在图片或文件夹的 Finder 一级右键菜单中显示此操作 |
| 保留原图备份 | 关 | 开启后，只在即将处理照片时，在同一目录保存完整原图，如 `照片.jpg_original` |

备份关闭时直接修改原文件。备份开启时，如果已有同名备份或备份失败，会保留原图并报告失败。需要恢复时，可将备份复制到其他目录，再删去副本文件名末尾的 `_original`。

处理成功和跳过均静默，只有失败会提示。普通 JPEG、主图自身为 HDR、未明确标记增益图和包含其他附图的文件会跳过。当前支持 MPF 中恰有一张主图和一张明确标记的 Gain Map 的 JPEG。

## 从源码构建

构建机需要 macOS、Xcode 或 Command Line Tools 和网络连接。打包脚本下载固定版本的工具、校验 SHA-256，并编译当前架构的 Perl。首次构建需要几分钟。

在项目根目录运行：

```sh
/usr/bin/python3 finder-menu/build_app.py --dmg build
```

App 和 DMG 都在 `build/`。重复构建时请指定新目录。可编辑的图标源文件在 [assets/AppIcon.svg](assets/AppIcon.svg)，修改后运行 `/usr/bin/python3 assets/build_icon.py` 重新生成 ICNS。

## 测试

```sh
/usr/bin/python3 packaging/verify_app.py "build/清除JPG HDR小工具.app"
```

测试通过 App 内的 Python 和 ExifTool 处理临时副本，检查主图、EXIF、ICC、文件属性、备份以及异常处理。测试图片来自 libavif，来源和许可见 [tests/fixtures](tests/fixtures/)。
