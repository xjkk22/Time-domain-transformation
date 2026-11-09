import argparse
import sys

import pandas as pd
import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path
from tempfile import NamedTemporaryFile
from scipy.signal import czt, get_window, savgol_filter

# ==========================================
# 0. 全局设置
# ==========================================
plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False


# ==========================================
# 1. 基础函数
# ==========================================
def load_data(filename):
    df = pd.read_csv(filename)
    freqs = df["Freq(MHz)"].to_numpy(dtype=float) * 1e6
    mag_db = df["ReflectRatio(dB)"].to_numpy(dtype=float)
    if len(freqs) < 2 or not np.isfinite(freqs).all() or not np.isfinite(mag_db).all():
        raise ValueError("CSV 至少需要两行有效数值，频率和反射率必须为有限值。")
    steps = np.diff(freqs)
    if (steps <= 0).any() or not np.allclose(steps, steps[0], rtol=1e-6, atol=2):
        raise ValueError("ICZT 要求频率按升序等间隔采样。")
    mag_linear = 10 ** (mag_db / 20.0)
    return freqs, mag_linear.astype(complex), mag_db  # 假设相位为 0


def freq_to_time_iczt(S_params, freqs, t_start, t_stop, num_points):
    """ICZT: 频域 -> 时域"""
    N = len(S_params)
    f_step = freqs[1] - freqs[0]
    f_start = freqs[0]

    # 频域加窗以减少时域旁瓣
    #window = np.hanning(N)
    window = np.ones(N)
    S_params_win = S_params * window

    # ICZT 参数计算
    t_axis = np.linspace(t_start, t_stop, num_points)
    delta_t = t_axis[1] - t_axis[0]
    A = np.exp(-1j * 2 * np.pi * f_step * t_start)
    W = np.exp(1j * 2 * np.pi * f_step * delta_t)

    td_raw = czt(S_params_win, m=num_points, w=W, a=A)
    phase_correction = np.exp(1j * 2 * np.pi * f_start * t_axis)
    td_data = td_raw * phase_correction

    return t_axis, td_data


# ==========================================
# 2. 核心功能：双边时域门 (New!)
# ==========================================
def apply_dual_time_gate(t_axis, td_data, gate_start, gate_stop, taper_time=0.18e-9):
    """
    应用双边时域门：保留 [gate_start, gate_stop] 之间的信号
    """
    # 创建带汉宁过渡边的软时间门，避免硬截断导致频域失真
    mask = np.zeros_like(t_axis, dtype=float)
    passband = (t_axis >= gate_start) & (t_axis <= gate_stop)
    mask[passband] = 1.0

    left_taper = (t_axis >= gate_start - taper_time) & (t_axis < gate_start)
    if np.any(left_taper):
        x = (t_axis[left_taper] - (gate_start - taper_time)) / taper_time
        mask[left_taper] = 0.5 - 0.5 * np.cos(np.pi * x)

    right_taper = (t_axis > gate_stop) & (t_axis <= gate_stop + taper_time)
    if np.any(right_taper):
        x = (t_axis[right_taper] - gate_stop) / taper_time
        mask[right_taper] = 0.5 + 0.5 * np.cos(np.pi * x)

    td_gated = td_data * mask
    return td_gated, mask


def time_to_freq_dft(td_data, t_axis, freqs):
    """时域 -> 频域"""
    f_grid = freqs.reshape(-1, 1)
    t_grid = t_axis.reshape(1, -1)
    transform_matrix = np.exp(-1j * 2 * np.pi * f_grid * t_grid)
    s_freq_recovered = np.dot(transform_matrix, td_data)
    return s_freq_recovered


def smooth_trend(y, window_points=301):
    """提取慢变化趋势，用于校正门控后频段整体上/下偏移。"""
    window_points = int(window_points)
    if window_points < 3:
        return y
    if window_points % 2 == 0:
        window_points += 1
    window_points = min(window_points, len(y) if len(y) % 2 == 1 else len(y) - 1)

    pad = window_points // 2
    y_pad = np.pad(y, (pad, pad), mode="edge")
    kernel = np.ones(window_points) / window_points
    return np.convolve(y_pad, kernel, mode="valid")


def smooth_final_curve(y, window_points=121, polyorder=3):
    """平滑最终曲线，压低残余小波纹，同时保留主趋势。"""
    window_points = int(window_points)
    if window_points % 2 == 0:
        window_points += 1
    if window_points <= polyorder + 2:
        return y
    window_points = min(window_points, len(y) if len(y) % 2 == 1 else len(y) - 1)
    if window_points <= polyorder + 2:
        return y
    return savgol_filter(y, window_points, polyorder, mode="interp")


# ==========================================
# 3. 主程序
# ==========================================
TIME_START_NS, TIME_STOP_NS = -5.0, 5.0
WINDOWS = {"soft": "软时间窗", "hann": "汉宁窗", "boxcar": "矩形窗",
           "hamming": "汉明窗", "blackman": "布莱克曼窗"}


DEFAULT_GATE_NS = (-0.1, 0.1)


def load_time_response(filename):
    freqs, s_params, mag_db_raw = load_data(filename)
    t_axis, td_data = freq_to_time_iczt(
        s_params, freqs, TIME_START_NS * 1e-9, TIME_STOP_NS * 1e-9, 2500
    )
    return freqs, mag_db_raw, t_axis, td_data


def suggest_range(t_axis, td_data):
    # 靠近时域边界时整体平移，以保留默认窗宽。
    peak_ns = t_axis[np.argmax(np.abs(td_data))] * 1e9
    width_ns = DEFAULT_GATE_NS[1] - DEFAULT_GATE_NS[0]
    start = round(float(np.clip(
        peak_ns - width_ns / 2, TIME_START_NS, TIME_STOP_NS - width_ns
    )), 3)
    return peak_ns, (start, round(start + width_ns, 3))


def inspect_range(filename):
    _, _, t_axis, td_data = load_time_response(filename)
    return suggest_range(t_axis, td_data)


def process_file(filename, window, gate_range=None, interactive=False, *, csv_path=None):
    if csv_path is not None:
        if csv_path.resolve() == filename.resolve():
            raise ValueError("导出路径与输入 CSV 相同，请选择其他目录。")
        if not csv_path.parent.is_dir():
            raise ValueError("CSV 导出目录不存在，请选择有效的文件夹。")
    freqs, mag_db_raw, t_axis, td_data = load_time_response(filename)
    if gate_range is None:
        peak_ns, gate_range = suggest_range(t_axis, td_data)
        suggested_start, suggested_stop = gate_range
        print(f"可用时间范围 [{TIME_START_NS:g}, {TIME_STOP_NS:g}] ns，"
              f"采样间隔约 {(t_axis[1] - t_axis[0]) * 1e9:.4f} ns。")
        print(f"时域主峰 {peak_ns:.3f} ns，建议起始范围 [{suggested_start:g}, {suggested_stop:g}] ns。")
        if interactive:
            choice = input("选择范围方式 [1] 建议范围 / 2 手动输入 / 3 原默认范围：").strip() or "1"
            if choice == "2":
                gate_range = (float(input("窗起点（ns）：")), float(input("窗终点（ns）：")))
            elif choice == "3":
                gate_range = DEFAULT_GATE_NS
            elif choice != "1":
                raise ValueError("请选择 1、2 或 3。")

    gate_start_ns, gate_stop_ns = gate_range
    if not TIME_START_NS <= gate_start_ns < gate_stop_ns <= TIME_STOP_NS:
        raise ValueError("窗范围必须满足 -5 ≤ 起点 < 终点 ≤ 5（ns）。")

    gate_start, gate_stop = gate_start_ns * 1e-9, gate_stop_ns * 1e-9
    inside = (t_axis >= gate_start) & (t_axis <= gate_stop)
    gate_points = np.count_nonzero(inside)
    if gate_points < (1 if window == "boxcar" else 3):
        raise ValueError("窗范围内的时域采样点不足，请扩大范围。")

    if window == "soft":
        td_gated, gate_mask = apply_dual_time_gate(t_axis, td_data, gate_start, gate_stop, 0.20e-9)
    else:
        gate_mask = np.zeros_like(t_axis)
        gate_mask[inside] = get_window(window, gate_points, fftbins=False)
        td_gated = td_data * gate_mask

    RAW_BLEND = 0.5
    BASELINE_CORRECTION_WINDOW = 501
    FINAL_SMOOTH_WINDOW = 121

    # --- 步骤 C: 反变换回频域 ---
    s_smooth_complex = time_to_freq_dft(td_gated, t_axis, freqs)

    # 频域校准与 dB 计算
    mag_smooth_linear = np.abs(s_smooth_complex)
    mag_gate_db = 20 * np.log10(mag_smooth_linear + 1e-12)

    # 用 dB 中位数做整体校准，比线性均值缩放更不容易被尖峰拉偏
    offset_db = np.median(mag_db_raw - mag_gate_db)
    mag_gate_db = mag_gate_db + offset_db

    # 校正门控后随频率缓慢变化的基线偏移，避免高频端整体向下
    baseline_error_db = smooth_trend(mag_db_raw - mag_gate_db, BASELINE_CORRECTION_WINDOW)
    mag_gate_db = mag_gate_db + baseline_error_db

    # 保留一部分原始曲线趋势，避免时域门控后的结果过度失真
    mag_smooth_db = RAW_BLEND * mag_db_raw + (1 - RAW_BLEND) * mag_gate_db
    mag_smooth_db = smooth_final_curve(mag_smooth_db, FINAL_SMOOTH_WINDOW)

    # --- 时域 dB 转换与归一化 ---
    td_abs = np.abs(td_data)
    td_abs_gated = np.abs(td_gated)

    td_db_raw = 20 * np.log10(td_abs + 1e-12)
    td_db_gated = 20 * np.log10(td_abs_gated + 1e-12)

    # 以主峰为 0dB 基准
    ref_max = np.max(td_db_raw)
    td_db_raw -= ref_max
    td_db_gated -= ref_max

    # 修正显示下限
    td_db_gated[td_db_gated < -100] = -100

    # ==========================================
    # 4. 绘图展示
    # ==========================================
    fig, axes = plt.subplots(3, 1, figsize=(10, 13), constrained_layout=True)

    # 图 1: 原始频域
    axes[0].plot(freqs / 1e6, mag_db_raw, color='royalblue', linewidth=1, alpha=0.7, label='原始测量数据')
    axes[0].set_title('(1) 原始频域反射率：包含环境干扰引起的剧烈波动', fontsize=12, fontweight='bold')
    axes[0].set_ylabel('反射率 (dB)')
    axes[0].grid(True, linestyle=':', alpha=0.6)
    axes[0].legend(loc='upper right')

    # 图 2: 时域 dB 图
    ax2 = axes[1]
    ax2.plot(t_axis * 1e9, td_db_raw, color='darkgray', linewidth=1, alpha=0.6, label='原始时域响应')
    ax2.plot(t_axis * 1e9, td_db_gated, 'r-', linewidth=1.5, label='加窗后信号')

    # 突出显示加窗范围
    gate_label = f"{WINDOWS[window]} [{gate_start_ns:g}, {gate_stop_ns:g}] ns"
    if window == "soft":
        gate_label += "，单侧过渡 0.20 ns"
    ax2.fill_between(t_axis * 1e9, -100, 10, where=(gate_mask > 0),
                     color='green', alpha=0.1, label=gate_label)

    ax2.set_title(f'(2) 时域脉冲响应 (dB)：显示范围 {TIME_START_NS:.0f}ns 至 {TIME_STOP_NS:.0f}ns',
                  fontsize=12, fontweight='bold')
    ax2.set_xlabel('时间 (ns)')
    ax2.set_ylabel('相对幅度 (dB)')
    ax2.set_xlim(TIME_START_NS, TIME_STOP_NS)  # 横轴范围从 -5ns 开始
    ax2.set_ylim(-80, 5)
    ax2.grid(True, linestyle=':', alpha=0.6)
    ax2.legend(loc='upper right')

    # 图 3: 最终去噪结果对比
    axes[2].plot(freqs / 1e6, mag_db_raw, color='lightblue', linewidth=1, alpha=0.5, label='Raw (含干扰)')
    axes[2].plot(freqs / 1e6, mag_smooth_db, color='crimson', linewidth=2, label='Gated (去噪后)')

    axes[2].set_title('(3) 最终反射率曲线',
                      fontsize=12, fontweight='bold')
    axes[2].set_xlabel('频率 (MHz)')
    axes[2].set_ylabel('反射率 (dB)')
    axes[2].grid(True, linestyle=':', alpha=0.6)
    axes[2].legend(loc='upper right')


    overview_image = filename.with_name(f"{filename.stem}_overview.png")
    denoised_curve_image = filename.with_name(f"{filename.stem}_denoised_compare.png")
    fig_denoised, ax_denoised = plt.subplots(1, 1, figsize=(10, 5), constrained_layout=True)
    ax_denoised.plot(freqs / 1e6, mag_db_raw, color='lightblue', linewidth=1, alpha=0.55, label='Raw (含干扰)')
    ax_denoised.plot(freqs / 1e6, mag_smooth_db, color='crimson', linewidth=2, label='Denoised (去噪后)')
    ax_denoised.set_title('原始曲线与去噪后曲线对比', fontsize=13, fontweight='bold')
    ax_denoised.set_xlabel('频率 (MHz)')
    ax_denoised.set_ylabel('反射率 (dB)')
    ax_denoised.grid(True, linestyle=':', alpha=0.6)
    ax_denoised.legend(loc='upper right')
    try:
        fig.savefig(overview_image, dpi=300)
        fig_denoised.savefig(denoised_curve_image, dpi=300)
    finally:
        plt.close(fig)
        plt.close(fig_denoised)
    if csv_path is not None:
        # 先写入同目录的临时文件，写完后再替换目标文件。
        data = pd.DataFrame({"Freq(MHz)": freqs / 1e6, "ReflectRatio(dB)": mag_smooth_db})
        with NamedTemporaryFile(dir=csv_path.parent, delete=False) as temp:
            temp_path = Path(temp.name)
        try:
            data.to_csv(temp_path, index=False, encoding="utf-8-sig")
            temp_path.replace(csv_path)
        finally:
            temp_path.unlink(missing_ok=True)
    return overview_image, denoised_curve_image


def main():
    if sys.argv[1:] == ["--gui"] or (len(sys.argv) == 1 and sys.stdin is None):
        from chirpz_gui import launch

        launch(process_file, inspect_range, WINDOWS, DEFAULT_GATE_NS, (TIME_START_NS, TIME_STOP_NS))
        return 0

    parser = argparse.ArgumentParser(description="Chirp-Z 时域门控去噪并保存两张 PNG 图片",
                                     epilog="使用 --gui 打开图形界面。")
    parser.add_argument("filename", type=Path, help="输入 CSV 路径")
    parser.add_argument("--window", choices=WINDOWS, default="soft", help="窗类型，默认 soft")
    ranges = parser.add_mutually_exclusive_group()
    ranges.add_argument("--range", dest="gate_range", type=float, nargs=2,
                        metavar=("START_NS", "STOP_NS"),
                        help="窗起点与终点（ns），默认 -0.1 0.1")
    ranges.add_argument("--auto-range", action="store_true", help="围绕数据的时域主峰自动选窗")
    interactive = len(sys.argv) == 1
    try:
        if interactive:
            filename = Path(input("请输入 CSV 文件名或路径：").strip().strip('"'))
            print("  ".join(f"{key} = {name}" for key, name in WINDOWS.items()))
            window = input("窗类型 [soft]：").strip().lower() or "soft"
            if window not in WINDOWS:
                raise ValueError("请选择列表中的窗类型。")
            gate_range = None
        else:
            args = parser.parse_args()
            filename, window = args.filename, args.window
            gate_range = None if args.auto_range else args.gate_range or DEFAULT_GATE_NS

        for image_path in process_file(filename.resolve(), window, gate_range, interactive):
            print(f"已保存 {image_path}")
        return 0
    except (OSError, ValueError, KeyError, EOFError) as exc:
        print(f"处理失败：{exc}", file=sys.stderr)
        return 1
    finally:
        if interactive and sys.stdin.isatty():
            input("按 Enter 退出。")


if __name__ == "__main__":
    sys.exit(main())
