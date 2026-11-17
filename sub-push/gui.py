import json
import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox


CURRENT_DIR = os.path.dirname(__file__)
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from src import settings


class ConfigGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("sub-push 配置中心")
        self.geometry("760x520")
        self.resizable(True, True)

        self.cfg = settings.CONFIG
        self.vars = {}
        self._build()

    def _add_entry(self, parent, label, key_path, value):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=8, pady=5)
        ttk.Label(row, text=label, width=26).pack(side="left")
        var = tk.StringVar(value=str(value))
        ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)
        self.vars[key_path] = var

    def _build(self):
        canvas = tk.Canvas(self)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        content = ttk.Frame(canvas)
        content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        ttk.Label(content, text="运行配置", font=("Arial", 12, "bold")).pack(anchor="w", padx=8, pady=6)
        self._add_entry(content, "日志等级", "app.log_level", self.cfg["app"]["log_level"])
        self._add_entry(content, "定时间隔(分钟)", "app.schedule_interval_minutes", self.cfg["app"]["schedule_interval_minutes"])

        ttk.Label(content, text="FTP 配置", font=("Arial", 12, "bold")).pack(anchor="w", padx=8, pady=6)
        self._add_entry(content, "FTP Host", "ftp.host", self.cfg["ftp"]["host"])
        self._add_entry(content, "FTP Port", "ftp.port", self.cfg["ftp"]["port"])
        self._add_entry(content, "FTP User", "ftp.user", self.cfg["ftp"]["user"])
        self._add_entry(content, "FTP Password", "ftp.password", self.cfg["ftp"]["password"])
        self._add_entry(content, "Web Prefix", "ftp.web_prefix", self.cfg["ftp"]["web_prefix"])

        ttk.Label(content, text="数据源开关", font=("Arial", 12, "bold")).pack(anchor="w", padx=8, pady=6)
        self._add_entry(content, "Mercari Enabled(true/false)", "sources.mercari.enabled", self.cfg["sources"]["mercari"]["enabled"])
        self._add_entry(content, "PayPay Enabled(true/false)", "sources.paypay.enabled", self.cfg["sources"]["paypay"]["enabled"])

        btn_row = ttk.Frame(content)
        btn_row.pack(fill="x", padx=8, pady=12)
        ttk.Button(btn_row, text="保存配置", command=self.save).pack(side="left")
        ttk.Button(btn_row, text="打开配置文件", command=self.open_config_path).pack(side="left", padx=8)

    def _set_by_path(self, cfg, path, val):
        keys = path.split(".")
        cur = cfg
        for k in keys[:-1]:
            cur = cur[k]
        last = keys[-1]
        raw = str(val).strip()
        if raw.lower() in ("true", "false"):
            cur[last] = raw.lower() == "true"
            return
        try:
            cur[last] = int(raw)
        except ValueError:
            cur[last] = raw

    def save(self):
        cfg = json.loads(json.dumps(self.cfg, ensure_ascii=False))
        for path, var in self.vars.items():
            self._set_by_path(cfg, path, var.get())
        try:
            settings.save_config(cfg)
            messagebox.showinfo("成功", f"配置已保存到:\n{settings.CONFIG_PATH}")
        except Exception as exc:
            messagebox.showerror("失败", str(exc))

    def open_config_path(self):
        messagebox.showinfo("配置文件路径", settings.CONFIG_PATH)


if __name__ == "__main__":
    app = ConfigGUI()
    app.mainloop()

