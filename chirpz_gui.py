from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from shutil import copy2
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk


class ChirpZWindow:
    def __init__(self, root, process_file, inspect_range, windows, default_range, time_limits):
        self.root = root
        self.process_file = process_file
        self.inspect_range = inspect_range
        self.window_keys = {label: key for key, label in windows.items()}
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.job = None
        self.paths = ()
        self.images = []
        self.preview_images = []
        self.preview_timer = None

        root.title("Chirp-Z · TD 时域门控去噪")
        root.geometry("1080x820")
        root.minsize(780, 520)
        root.protocol("WM_DELETE_WINDOW", self.close)

        self.filename = tk.StringVar()
        self.window = tk.StringVar(value=next(iter(windows.values())))
        self.start = tk.StringVar(value=f"{default_range[0]:g}")
        self.stop = tk.StringVar(value=f"{default_range[1]:g}")
        self.range_info = tk.StringVar()
        self.export_csv = tk.BooleanVar(value=False)
        program = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__)
        self.default_csv_dir = program.resolve().parent
        self.csv_dir = tk.StringVar(value=str(self.default_csv_dir))
        self.status = tk.StringVar(value="请选择 CSV 文件，然后点击处理。")

        controls = ttk.Frame(root, padding=(12, 12, 12, 6))
        controls.pack(fill="x")
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="数据文件").grid(row=0, column=0, padx=(0, 8))
        self.path_entry = ttk.Entry(controls, textvariable=self.filename)
        self.path_entry.grid(row=0, column=1, sticky="ew")
        self.browse_button = ttk.Button(controls, text="浏览…", command=self.browse)
        self.browse_button.grid(row=0, column=2, padx=(8, 0))

        options = ttk.Frame(controls)
        options.grid(row=1, column=0, columnspan=3, sticky="w", pady=(10, 4))
        ttk.Label(options, text="窗类型").pack(side="left")
        ttk.Combobox(options, textvariable=self.window, values=tuple(windows.values()),
                     state="readonly", width=11).pack(side="left", padx=(5, 12))
        ttk.Label(options, text="起点 (ns)").pack(side="left")
        ttk.Entry(options, textvariable=self.start, width=8).pack(side="left", padx=(5, 12))
        ttk.Label(options, text="终点 (ns)").pack(side="left")
        ttk.Entry(options, textvariable=self.stop, width=8).pack(side="left", padx=(5, 12))
        self.suggest_button = ttk.Button(options, text="填入建议范围", command=self.suggest)
        self.suggest_button.pack(side="left", padx=(0, 8))
        self.process_button = ttk.Button(options, text="处理", command=self.process)
        self.process_button.pack(side="left", padx=(0, 8))
        self.save_button = ttk.Button(options, text="另存图片…", command=self.save, state="disabled")
        self.save_button.pack(side="left")

        self.limit_text = f"可用时间范围 {time_limits[0]:g} 至 {time_limits[1]:g} ns"
        self.range_info.set(self.limit_text)
        ttk.Label(controls, textvariable=self.range_info).grid(row=2, column=0, columnspan=3, sticky="w")
        self.filename.trace_add("write", lambda *_: self.range_info.set(self.limit_text))

        export = ttk.Frame(controls)
        export.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        export.columnconfigure(1, weight=1)
        self.csv_check = ttk.Checkbutton(export, text="导出去噪 CSV", variable=self.export_csv,
                                         command=self.update_csv_controls)
        self.csv_check.grid(row=0, column=0, padx=(0, 8))
        self.csv_entry = ttk.Entry(export, textvariable=self.csv_dir)
        self.csv_entry.grid(row=0, column=1, sticky="ew")
        self.csv_browse = ttk.Button(export, text="选择目录…", command=self.browse_csv_dir)
        self.csv_browse.grid(row=0, column=2, padx=(8, 0))
        self.update_csv_controls()

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self.previews = []
        for title in ("处理总览", "去噪对比"):
            canvas = tk.Canvas(notebook, background="#f4f4f4", highlightthickness=0)
            notebook.add(canvas, text=title)
            canvas.bind("<Configure>", self.schedule_preview)
            self.previews.append(canvas)
        notebook.bind("<<NotebookTabChanged>>", self.schedule_preview)
        ttk.Label(root, textvariable=self.status, padding=(12, 4, 12, 8)).pack(fill="x")

    def browse(self):
        filename = filedialog.askopenfilename(filetypes=[("CSV 数据文件", "*.csv"), ("所有文件", "*.*")])
        if filename:
            self.filename.set(filename)

    def update_csv_controls(self):
        self.csv_check.state(["disabled" if self.job is not None else "!disabled"])
        state = "!disabled" if self.export_csv.get() and self.job is None else "disabled"
        for widget in (self.csv_entry, self.csv_browse):
            widget.state([state])

    def browse_csv_dir(self):
        directory = filedialog.askdirectory(initialdir=self.csv_dir.get() or self.default_csv_dir,
                                            title="选择 CSV 导出目录")
        if directory:
            self.csv_dir.set(directory)

    def start_job(self, task, on_success, *args, **kwargs):
        filename = self.filename.get().strip().strip('"')
        if not filename:
            messagebox.showwarning("缺少数据文件", "请选择或输入 CSV 文件路径。")
            return
        self.status.set("正在处理，请稍候…")
        for widget in (self.path_entry, self.browse_button, self.suggest_button, self.process_button):
            widget.state(["disabled"])
        self.save_button.state(["disabled"])
        self.job = self.executor.submit(task, Path(filename).resolve(), *args, **kwargs)
        self.update_csv_controls()
        self.root.after(75, self.poll, on_success)

    def poll(self, on_success):
        if not self.job.done():
            self.root.after(75, self.poll, on_success)
            return
        try:
            on_success(self.job.result())
        except Exception as exc:
            self.status.set("操作失败，请检查输入。")
            messagebox.showerror("操作失败", str(exc))
        finally:
            self.job = None
            self.update_csv_controls()
            for widget in (self.path_entry, self.browse_button, self.suggest_button, self.process_button):
                widget.state(["!disabled"])
            if self.paths:
                self.save_button.state(["!disabled"])

    def suggest(self):
        self.start_job(self.inspect_range, self.show_suggestion)

    def show_suggestion(self, result):
        peak, (start, stop) = result
        self.start.set(f"{start:g}")
        self.stop.set(f"{stop:g}")
        self.range_info.set(f"{self.limit_text}  |  时域主峰 {peak:.3f} ns  |  建议 {start:g} 至 {stop:g} ns")
        self.status.set("已填入建议范围，可修改起止值后点击处理。")

    def process(self):
        try:
            gate_range = float(self.start.get()), float(self.stop.get())
        except ValueError:
            messagebox.showwarning("时间范围无效", "起点和终点请输入数字，单位为 ns。")
            return
        csv_path = None
        filename = self.filename.get().strip().strip('"')
        if self.export_csv.get() and filename:
            directory = self.csv_dir.get().strip().strip('"')
            destination = Path(directory).expanduser() if directory else self.default_csv_dir
            if not destination.is_dir():
                messagebox.showwarning("导出目录无效", "请选择有效的 CSV 导出目录。")
                return
            csv_path = destination.resolve() / f"{Path(filename).stem}_denoised.csv"
            if csv_path.resolve() == Path(filename).resolve():
                messagebox.showwarning("导出路径无效", "导出文件与输入 CSV 相同，请选择其他目录。")
                return
            if csv_path.exists() and not messagebox.askyesno("覆盖 CSV", f"是否覆盖已有文件？\n{csv_path}"):
                return
        self.start_job(self.process_file, partial(self.show_results, csv_path=csv_path),
                       self.window_keys[self.window.get()], gate_range, csv_path=csv_path)

    def show_results(self, paths, *, csv_path=None):
        images = []
        for path in paths:
            with Image.open(path) as image:
                images.append(image.copy())
        self.paths, self.images = paths, images
        if csv_path is None:
            self.status.set(f"已自动保存两张 PNG 至 {paths[0].parent}")
        else:
            self.status.set(f"已保存两张 PNG，并导出 CSV 至 {csv_path}")
        self.schedule_preview()

    def schedule_preview(self, _event=None):
        if self.preview_timer is not None:
            self.root.after_cancel(self.preview_timer)
        self.preview_timer = self.root.after(80, self.render_preview)

    def render_preview(self):
        self.preview_timer = None
        self.preview_images = []
        for index, canvas in enumerate(self.previews):
            width, height = canvas.winfo_width(), canvas.winfo_height()
            canvas.delete("all")
            if not self.images:
                canvas.create_text(width // 2, height // 2, text="处理完成后在此预览结果", fill="#666666")
            elif width > 1 and height > 1:
                image = self.images[index].copy()
                image.thumbnail((width, height), Image.Resampling.LANCZOS)
                preview = ImageTk.PhotoImage(image, master=self.root)
                self.preview_images.append(preview)
                canvas.create_image(width // 2, height // 2, image=preview)

    def save(self):
        folder = filedialog.askdirectory(initialdir=self.paths[0].parent, title="选择图片保存目录")
        if not folder:
            return
        destination = Path(folder).resolve()
        targets = [destination / path.name for path in self.paths]
        if all(target == path for target, path in zip(targets, self.paths)):
            self.status.set(f"图片已保存在 {destination}")
            return
        if any(target.exists() for target in targets) and not messagebox.askyesno(
            "覆盖图片", "目标目录已有同名图片，是否覆盖？"
        ):
            return
        try:
            for path, target in zip(self.paths, targets):
                copy2(path, target)
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        self.status.set(f"已另存两张 PNG 至 {destination}")

    def close(self):
        if self.job is not None:
            self.status.set("请等待当前操作完成后再关闭。")
            return
        self.executor.shutdown(wait=False)
        self.root.destroy()


def launch(process_file, inspect_range, windows, default_range, time_limits):
    root = tk.Tk()
    root.iconbitmap(default=str(Path(__file__).resolve().with_name("xinchen.ico")))
    ChirpZWindow(root, process_file, inspect_range, windows, default_range, time_limits)
    root.mainloop()
