# 清除JPG HDR小工具

在 macOS Finder 或 Windows 资源管理器中选中 JPG、JPEG 或文件夹，右键选择「清除 JPG HDR 增益图」，即可移除明确标记的 HDR 增益图。主图无需重新压缩。文件夹只处理当前层，也支持多选。

## 清除后会影响画质吗？

**不会重新压缩 JPEG 主图。** 工具保留原有的主图压缩数据，因此分辨率和清晰度不变，也不会引入再次保存 JPEG 时可能产生的压缩损失。测试中，处理前后的主图压缩数据逐字节相同，抽样解码后的像素也完全一致。EXIF 和测试样本中已有的 ICC 色彩配置均保留。对于受支持的三图相机 JPG，内嵌预览图也会原样保留。

**HDR 屏幕上的高光会改变。** 增益图记录了如何从标准动态范围（SDR）主图还原 HDR 亮度。原本按 SDR 显示时，画面应保持一致。在原本会使用增益图的 iPhone 或 HDR 显示器上，灯光、反光等亮部会回到主图的亮度。删除后无法只凭处理过的 JPG 恢复原始增益图。要同时保留这两种呈现，请在处理前开启「保留原图备份」。 [Apple 对 HDR 增益图显示的说明](https://developer.apple.com/videos/play/wwdc2024/10177/?time=1195)

## 下载与安装

### Windows

下载并运行 [Windows 安装程序](https://github.com/Kikuku10/clear-jpg-hdr/releases/download/v1.3.0/ClearJPGHDR-1.3.0-windows-x64-setup.exe)，安装到当前账户。支持 Windows 10/11 x64 和可运行 x64 应用的 Windows 11 on Arm。Python、ExifTool 和 Perl 随安装包提供。从开始菜单打开「清除JPG HDR小工具」可以切换右键菜单、设置原图备份。卸载入口在 Windows「已安装的应用」中。

Windows 10 的操作在右键菜单内。Windows 11 普通安装后，入口在 **右键 → 显示更多选项 → 清除 JPG HDR 增益图**。设置窗口会显示当前菜单位置。系统已启用开发者模式，或部署方已为随附的菜单身份包提供 Windows 信任的签名时，可注册 Windows 11 新版一级菜单。

### macOS

1. 在 [macOS 下载页](https://github.com/Kikuku10/clear-jpg-hdr/releases/tag/v1.2.0) 下载适合自己 Mac 的 DMG。Apple 芯片选 `arm64`，Intel 选 `x86_64`。最低系统版本为 macOS 13。
2. 打开 DMG，将「清除JPG HDR小工具」拖到旁边的「Applications」文件夹。Python、Perl 和 ExifTool 均随 App 安装。
3. 从「应用程序」启动小工具，点击「管理扩展…」启用 Finder 扩展，然后右键选择照片或文件夹。

v1.2.0 使用本地签名，未经 Apple 公证。首次启动若被 Gatekeeper 拦截，可在核实下载来源后按 [Apple 的首次打开说明](https://support.apple.com/zh-cn/102445) 操作。

## 设置

双击「清除JPG HDR小工具」打开设置。

| 设置 | 默认值 | 作用 |
| --- | --- | --- |
| 显示「清除 JPG HDR 增益图」 | 开 | 在图片或文件夹的右键菜单中显示此操作 |
| 保留原图备份 | 关 | 开启后，只在即将处理照片时，在同一目录保存完整原图，如 `照片.jpg_original` |

备份关闭时直接修改原文件。备份开启时，如果已有同名备份或备份失败，会保留原图并报告失败。需要恢复时，可将备份复制到其他目录，再删去副本文件名末尾的 `_original`。

从 1.3.1 起，每次右键清除结束后都会弹出一次结果，例如「成功 1 个，跳过 2 个，失败 0 个」。跳过时会说明原因，失败时会列出文件名和原因。对已清除的照片再次操作会计入跳过。

支持 MPF 中明确标记为「主图＋增益图」的 JPEG，也支持已验证的「主图＋大预览图＋增益图」相机 JPEG。后一种只移除增益图及其声明，保留原有预览图，并更新多图索引。普通 JPEG、主图自身为 HDR、增益图未明确标记或多图结构无法安全识别时，工具会跳过该文件。

上方链接的 macOS 1.2.0 和 Windows 1.3.0 安装包只支持两图格式，成功或跳过时静默。三图支持和结果弹窗已包含在当前源码中。

## 从源码构建

### Windows

在 Windows 上准备 Visual Studio C++ 桌面生成工具（x64 和 ARM64 目标）、Windows SDK、.NET Framework 4.x 编译器、Python 3 和 NSIS 3。将 `makensis` 加入 PATH，并在仓库根目录的 PowerShell 执行：

```powershell
.\windows\build.ps1 -OutputDirectory build-native
py -3 packaging/windows/build_windows.py --native-dir build-native --output build-windows-x64
```

生成的安装程序在 `build-windows-x64/`。Windows 版的照片处理测试可针对已安装的程序执行：

```powershell
$app = Join-Path $env:LOCALAPPDATA 'Programs\ClearJPGHDR'
$env:CLEAR_HDR_TEST_ENGINE = Join-Path $app 'clear_hdr.py'
& "$app\runtime\python.exe" -I -B -X utf8 tests/test_clear_hdr.py -v
```

### macOS

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
