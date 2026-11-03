# Chirp-Z 时域门控工具使用说明

## 图形界面

在 64 位 Windows 上双击 `dist/ChirpZ-GUI.exe`，无需安装 Python。

1. 点击 **浏览…** 选择 CSV，也可直接输入路径。
2. 从下拉框选择窗类型，填写起止时间。点击 **填入建议范围** 可根据当前 CSV 计算时域主峰并填入建议值，填入后仍可手动调整。
3. 点击 **处理**，在 **处理总览** 和 **去噪对比** 标签页查看两张图。程序会自动保存到 CSV 所在目录。点击 **另存图片…** 可将两张 PNG 复制到其他目录。

## 命令行交互模式

在 64 位 Windows 上打开 `dist/ChirpZ-v2.exe`，根据提示输入 CSV 文件名或完整路径，再输入窗类型并选择时间范围。路径包含空格时，可在交互提示中直接粘贴完整路径。输入相对路径时，程序从当前工作目录查找文件。窗类型直接回车可使用软时间窗。

CSV 首行需包含 `Freq(MHz)` 和 `ReflectRatio(dB)` 两列。至少提供两行有效数据，频率按升序等间隔排列。算法沿用原脚本的零相位假设。

| 输入值 | 窗类型 | 范围含义 |
| --- | --- | --- |
| `soft` | 原脚本的软时间窗（默认） | 指定权重为 1 的区间，两侧各有 0.20 ns 的余弦过渡，过渡受时域边界限制 |
| `hann` | 汉宁窗 | 在指定区间内应用对称汉宁窗 |
| `boxcar` | 矩形窗 | 指定区间内权重为 1 |
| `hamming` | 汉明窗 | 在指定区间内应用对称汉明窗 |
| `blackman` | 布莱克曼窗 | 在指定区间内应用对称布莱克曼窗 |

选好窗类型后，程序会显示可用时间范围、采样间隔和当前数据的时域主峰。可选择：

1. **建议范围**（直接回车）。以时域主峰为中心取约 0.20 ns 宽的窗口，靠近边界时平移到可用范围内。适合作为试算起点，再根据输出总览图调整。由于输入只含幅度，零相位假设通常会让主峰出现在 0 ns 附近。主峰位置仅用于选窗参考。
2. **手动输入**。输入起点和终点，可用于保留指定的时域响应。
3. **原默认范围**。使用 −0.1 至 0.1 ns。

窗的起止值以纳秒（ns）为单位，必须满足 −5 ≤ 起点 < 终点 ≤ 5。窗口过窄、容纳的时域采样点不足时，程序会提示扩大范围。

## PowerShell 命令示例

在项目目录中运行以下命令，将路径替换为实际 CSV 文件路径。

```powershell
.\dist\ChirpZ-v2.exe "D:\测量数据\s5.csv" --window hann --range -0.2 0.2
.\dist\ChirpZ-v2.exe "D:\测量数据\s5.csv" --window boxcar --range -0.1 0.15
.\dist\ChirpZ-v2.exe "D:\测量数据\s5.csv" --window hann --auto-range
.\dist\ChirpZ-v2.exe --help
```

命令行省略范围参数时使用原默认范围。

## 自动输出

程序会把两张 300 DPI PNG 保存到输入 CSV 所在目录。例如，输入 `s5.csv` 时生成：

- `s5_overview.png`，包含原始频域、时域门控和最终频域对比三个子图。
- `s5_denoised_compare.png`，包含原始与去噪后频域曲线的单独对比图。

对同一输入重复运行会覆盖这两张图。请确保输入文件所在目录可写。

## 从源码运行或重新打包

在 Windows 上打开 PowerShell，进入项目目录后执行以下命令。生成的单文件 EXE 位于 `dist/ChirpZ-v2.exe`。

```powershell
python -m venv .build-env
.\.build-env\Scripts\python.exe -m pip install numpy scipy pandas matplotlib pyinstaller
.\.build-env\Scripts\python.exe "Chirp_Z逆变换_实例.py" "D:\测量数据\s5.csv" --window hann --range -0.2 0.2
.\.build-env\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --name ChirpZ-v2 --workpath .build\pyinstaller --specpath .build "Chirp_Z逆变换_实例.py"
```

图形界面也可从源码启动，或单独重新打包。

```powershell
.\.build-env\Scripts\python.exe "Chirp_Z逆变换_实例.py" --gui
.\.build-env\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name ChirpZ-GUI --workpath .build\pyinstaller --specpath .build "Chirp_Z逆变换_实例.py"
```
