import os
import sys
import subprocess
import threading
import time
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

import pandas as pd

# Tkinter imports
import tkinter as tk
from tkinter import ttk
from tkinter import filedialog
from tkinter import messagebox
from tkinter import scrolledtext

# Locate Repository Root
REPO_ROOT = Path(__file__).resolve().parents[3]

# Attempt driver discovery safely
try:
    import pyodbc
    AVAILABLE_DRIVERS = pyodbc.drivers()
except Exception:
    AVAILABLE_DRIVERS = []

# Default driver fallback logic
def get_best_driver() -> str:
    for driver in ["ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server", "SQL Server Native Client 11.0", "SQL Server"]:
        if driver in AVAILABLE_DRIVERS:
            return driver
    return AVAILABLE_DRIVERS[0] if AVAILABLE_DRIVERS else "ODBC Driver 17 for SQL Server"

# --- Pure, Testable Functions Outside Tkinter Mainloop ---

def check_run_dir_nesting(run_dir_str: str) -> bool:
    """
    Checks if the run directory has nested or duplicate 'runs' segments.
    Returns True if nesting or adjacent duplicates are detected, False otherwise.
    """
    if not run_dir_str.strip():
        return False
    path = Path(run_dir_str.strip())
    parts = [p.lower() for p in path.parts]
    if parts.count("runs") > 1:
        return True
    for i in range(len(parts) - 1):
        if parts[i] == parts[i + 1]:
            return True
    return False

def validate_inputs(
    answer_bak: str,
    submissions: str,
    config: str,
    test_data: str,
    run_dir: str,
    command: str
) -> List[str]:
    """
    Validates input paths based on the requested command.
    Returns a list of error strings. If empty, validation passed.
    """
    errors = []
    
    # 1. Config path is required for all commands
    config_val = config.strip()
    if not config_val:
        errors.append("Configuration file path is required.")
    else:
        cfg_path = Path(config_val)
        if not cfg_path.is_absolute():
            cfg_path = REPO_ROOT / cfg_path
        if not cfg_path.exists():
            errors.append(f"Configuration file does not exist: {config_val}")
            
    # 2. Run directory must not be empty
    run_dir_val = run_dir.strip()
    if not run_dir_val:
        errors.append("Run directory must not be empty.")
    elif check_run_dir_nesting(run_dir_val):
        errors.append("Run directory contains nested 'runs' segments or duplicate adjacent directories. Please correct it.")

    # 3. Command-specific validation
    if command in ("snapshot", "full"):
        ans_val = answer_bak.strip()
        if not ans_val:
            errors.append("Answer backup file (.bak) path is required.")
        else:
            ans_path = Path(ans_val)
            if not ans_path.is_absolute():
                ans_path = REPO_ROOT / ans_path
            if not ans_path.exists():
                errors.append(f"Answer backup file does not exist: {ans_val}")

        sub_val = submissions.strip()
        if not sub_val:
            errors.append("Student submissions folder path is required.")
        else:
            sub_path = Path(sub_val)
            if not sub_path.is_absolute():
                sub_path = REPO_ROOT / sub_path
            if not sub_path.exists():
                errors.append(f"Student submissions folder does not exist: {sub_val}")

    if command in ("test-views", "full"):
        td_val = test_data.strip()
        if not td_val:
            errors.append("Test data folder path is required.")
        else:
            td_path = Path(td_val)
            if not td_path.is_absolute():
                td_path = REPO_ROOT / td_path
            if not td_path.exists():
                errors.append(f"Test data folder does not exist: {td_val}")

    return errors

def build_snapshot_command(answer_bak: str, submissions: str, run_dir: str, config: str) -> List[str]:
    return [
        sys.executable,
        "src/dbcheck/cli/main.py",
        "snapshot",
        "--answer-bak", str(Path(answer_bak.strip())),
        "--submissions", str(Path(submissions.strip())),
        "--run-dir", str(Path(run_dir.strip())),
        "--config", str(Path(config.strip()))
    ]

def build_compare_structure_command(run_dir: str, config: str) -> List[str]:
    return [
        sys.executable,
        "src/dbcheck/cli/main.py",
        "compare-structure",
        "--run-dir", str(Path(run_dir.strip())),
        "--config", str(Path(config.strip()))
    ]

def build_test_views_command(run_dir: str, test_data: str, config: str, answer_bak: Optional[str] = None) -> List[str]:
    cmd = [
        sys.executable,
        "src/dbcheck/cli/main.py",
        "test-views",
        "--run-dir", str(Path(run_dir.strip())),
        "--test-data", str(Path(test_data.strip())),
        "--config", str(Path(config.strip()))
    ]
    if answer_bak and answer_bak.strip():
        cmd.extend(["--answer-bak", str(Path(answer_bak.strip()))])
    return cmd

def sanitize_text(text: str, secret: Optional[str]) -> str:
    """Removes sensitive password strings from logged text."""
    if secret and secret.strip() and secret in text:
        return text.replace(secret, "********")
    return text

# --- Tkinter Application UI Class ---

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("SQL Server Assignment Grader GUI")
        self.root.geometry("1000x850")
        self.root.minsize(900, 750)

        # Process management variables
        self.active_process: Optional[subprocess.Popen] = None
        self.pipeline_queue: List[tuple] = []  # List of (command_list, name)
        self.stop_requested = False
        self.running_thread: Optional[threading.Thread] = None

        # Build GUI layouts
        self._setup_styles()
        self._create_widgets()
        self._load_defaults()

    def _setup_styles(self):
        self.style = ttk.Style()
        self.style.theme_use("clam")
        
        # Configure frames and buttons with clean modern colors
        self.style.configure(".", font=("Segoe UI", 10))
        self.style.configure("TLabel", foreground="#333333")
        self.style.configure("TButton", padding=6, relief="flat", background="#e1e1e1")
        self.style.map("TButton",
            background=[("active", "#d0d0d0"), ("disabled", "#f0f0f0")],
            foreground=[("disabled", "#a0a0a0")]
        )
        self.style.configure("Primary.TButton", background="#007acc", foreground="white")
        self.style.map("Primary.TButton", background=[("active", "#005999"), ("disabled", "#f0f0f0")])
        self.style.configure("Stop.TButton", background="#d9534f", foreground="white")
        self.style.map("Stop.TButton", background=[("active", "#c9302c"), ("disabled", "#f0f0f0")])

    def _create_widgets(self):
        # Configure master layout grid
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)  # Log & table section should scale

        # ----------------------------------------------------
        # Frame A: Input Paths
        # ----------------------------------------------------
        path_frame = ttk.LabelFrame(self.root, text=" Input Paths ", padding=10)
        path_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=5)
        path_frame.columnconfigure(1, weight=1)

        # Answer Backup
        ttk.Label(path_frame, text="Answer Backup (.bak):").grid(row=0, column=0, sticky="w", pady=3)
        self.ans_bak_var = tk.StringVar()
        ttk.Entry(path_frame, textvariable=self.ans_bak_var).grid(row=0, column=1, sticky="ew", padx=5, pady=3)
        ttk.Button(path_frame, text="Browse...", command=self._browse_ans_bak).grid(row=0, column=2, pady=3)

        # Student Submissions
        ttk.Label(path_frame, text="Submissions Folder:").grid(row=1, column=0, sticky="w", pady=3)
        self.subs_var = tk.StringVar()
        ttk.Entry(path_frame, textvariable=self.subs_var).grid(row=1, column=1, sticky="ew", padx=5, pady=3)
        ttk.Button(path_frame, text="Browse...", command=self._browse_subs).grid(row=1, column=2, pady=3)

        # Config File
        ttk.Label(path_frame, text="Config (.yaml):").grid(row=2, column=0, sticky="w", pady=3)
        self.config_var = tk.StringVar()
        ttk.Entry(path_frame, textvariable=self.config_var).grid(row=2, column=1, sticky="ew", padx=5, pady=3)
        ttk.Button(path_frame, text="Browse...", command=self._browse_config).grid(row=2, column=2, pady=3)

        # Test Data Folder
        ttk.Label(path_frame, text="Test Data Folder:").grid(row=3, column=0, sticky="w", pady=3)
        self.test_data_var = tk.StringVar()
        ttk.Entry(path_frame, textvariable=self.test_data_var).grid(row=3, column=1, sticky="ew", padx=5, pady=3)
        ttk.Button(path_frame, text="Browse...", command=self._browse_test_data).grid(row=3, column=2, pady=3)

        # Run Directory
        ttk.Label(path_frame, text="Run Directory:").grid(row=4, column=0, sticky="w", pady=3)
        self.run_dir_var = tk.StringVar()
        
        run_dir_subframe = ttk.Frame(path_frame)
        run_dir_subframe.grid(row=4, column=1, sticky="ew", padx=5, pady=3)
        run_dir_subframe.columnconfigure(0, weight=1)
        
        ttk.Entry(run_dir_subframe, textvariable=self.run_dir_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(run_dir_subframe, text="🔄 Refresh Name", command=self._refresh_run_dir).grid(row=0, column=1, padx=(5, 0))
        
        ttk.Button(path_frame, text="Browse...", command=self._browse_run_dir).grid(row=4, column=2, pady=3)

        # ----------------------------------------------------
        # Frame B: SQL Server Settings
        # ----------------------------------------------------
        sql_frame = ttk.LabelFrame(self.root, text=" SQL Server Connection Settings ", padding=10)
        sql_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
        sql_frame.columnconfigure(1, weight=1)
        sql_frame.columnconfigure(3, weight=1)

        # Server name
        ttk.Label(sql_frame, text="Server Name:").grid(row=0, column=0, sticky="w", pady=3)
        self.server_var = tk.StringVar(value=".")
        ttk.Entry(sql_frame, textvariable=self.server_var).grid(row=0, column=1, sticky="ew", padx=5, pady=3)

        # Driver
        ttk.Label(sql_frame, text="Driver:").grid(row=0, column=2, sticky="w", pady=3)
        self.driver_var = tk.StringVar()
        self.driver_combo = ttk.Combobox(sql_frame, textvariable=self.driver_var, values=AVAILABLE_DRIVERS)
        self.driver_combo.grid(row=0, column=3, sticky="ew", padx=5, pady=3)
        if not AVAILABLE_DRIVERS:
            # Tolerant display when pyodbc has errors
            self.driver_var.set("ODBC Driver 17 for SQL Server")
            self.log("[WARNING] Could not discover SQL Server drivers via pyodbc. Driver field remains editable.\n")

        # Authentication mode
        ttk.Label(sql_frame, text="Authentication:").grid(row=1, column=0, sticky="w", pady=3)
        self.auth_var = tk.StringVar(value="Windows Authentication")
        self.auth_combo = ttk.Combobox(
            sql_frame, 
            textvariable=self.auth_var, 
            values=["Windows Authentication", "SQL Server Authentication"], 
            state="readonly"
        )
        self.auth_combo.grid(row=1, column=1, sticky="ew", padx=5, pady=3)
        self.auth_combo.bind("<<ComboboxSelected>>", self._on_auth_change)

        # Username
        self.user_label = ttk.Label(sql_frame, text="Username:")
        self.user_label.grid(row=1, column=2, sticky="w", pady=3)
        self.user_var = tk.StringVar()
        self.user_entry = ttk.Entry(sql_frame, textvariable=self.user_var)
        self.user_entry.grid(row=1, column=3, sticky="ew", padx=5, pady=3)

        # Password
        self.pass_label = ttk.Label(sql_frame, text="Password:")
        self.pass_label.grid(row=2, column=2, sticky="w", pady=3)
        self.pass_var = tk.StringVar()
        self.pass_entry = ttk.Entry(sql_frame, textvariable=self.pass_var, show="*")
        self.pass_entry.grid(row=2, column=3, sticky="ew", padx=5, pady=3)

        # Trust Cert checkbox
        self.trust_var = tk.BooleanVar(value=True)
        self.trust_check = ttk.Checkbutton(sql_frame, text="Trust Server Certificate (Encrypt=No)", variable=self.trust_var)
        self.trust_check.grid(row=2, column=0, columnspan=2, sticky="w", pady=3)

        self._update_auth_fields_state()

        # ----------------------------------------------------
        # Log Panel & Summary Preview Notebook/Paned view
        # ----------------------------------------------------
        middle_pane = ttk.PanedWindow(self.root, orient="vertical")
        middle_pane.grid(row=2, column=0, sticky="nsew", padx=10, pady=5)

        # Top half of pane: Logs
        log_frame = ttk.LabelFrame(middle_pane, text=" Execution Logs ", padding=5)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, font=("Consolas", 9), bg="#fafafa")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        middle_pane.add(log_frame, weight=1)

        # Bottom half of pane: Summary Preview
        preview_frame = ttk.LabelFrame(middle_pane, text=" Grading Summary Preview ", padding=5)
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)

        tree_scroll_y = ttk.Scrollbar(preview_frame, orient="vertical")
        tree_scroll_x = ttk.Scrollbar(preview_frame, orient="horizontal")

        self.tree = ttk.Treeview(
            preview_frame, 
            yscrollcommand=tree_scroll_y.set, 
            xscrollcommand=tree_scroll_x.set, 
            selectmode="browse"
        )
        tree_scroll_y.config(command=self.tree.yview)
        tree_scroll_x.config(command=self.tree.xview)

        self.tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll_y.grid(row=0, column=1, sticky="ns")
        tree_scroll_x.grid(row=1, column=0, sticky="ew")

        middle_pane.add(preview_frame, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        # ----------------------------------------------------
        # Action Control Panel
        # ----------------------------------------------------
        action_frame = ttk.Frame(self.root, padding=5)
        action_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=5)

        # Execute buttons
        self.btn_snap = ttk.Button(action_frame, text="1. Create Snapshot", command=lambda: self._run_command_pipeline("snapshot"))
        self.btn_snap.pack(side="left", padx=5)

        self.btn_comp = ttk.Button(action_frame, text="2. Compare Structure", command=lambda: self._run_command_pipeline("compare-structure"))
        self.btn_comp.pack(side="left", padx=5)

        self.btn_views = ttk.Button(action_frame, text="3. Test Views", command=lambda: self._run_command_pipeline("test-views"))
        self.btn_views.pack(side="left", padx=5)

        self.btn_full = ttk.Button(action_frame, text="Run Full Pipeline", style="Primary.TButton", command=lambda: self._run_command_pipeline("full"))
        self.btn_full.pack(side="left", padx=5)

        self.btn_stop = ttk.Button(action_frame, text="Stop Current Process", style="Stop.TButton", command=self._stop_process)
        self.btn_stop.pack(side="left", padx=5)
        self.btn_stop.config(state="disabled")

        # ----------------------------------------------------
        # Navigation & Reporting Frame
        # ----------------------------------------------------
        nav_frame = ttk.Frame(self.root, padding=5)
        nav_frame.grid(row=4, column=0, sticky="ew", padx=10, pady=5)

        self.btn_open_run = ttk.Button(nav_frame, text="Open Run Folder", command=self._open_run_folder)
        self.btn_open_run.pack(side="left", padx=5)

        self.btn_open_summary = ttk.Button(nav_frame, text="Open Summary CSV", command=self._open_summary_csv)
        self.btn_open_summary.pack(side="left", padx=5)

        self.btn_open_reports = ttk.Button(nav_frame, text="Open Mapping Reports", command=self._open_mapping_reports)
        self.btn_open_reports.pack(side="left", padx=5)
        self.btn_open_reports.config(state="disabled")

        self.all_action_buttons = [self.btn_snap, self.btn_comp, self.btn_views, self.btn_full]

    # --- Defaults and Browse Handlers ---

    def _load_defaults(self):
        self.ans_bak_var.set("solution/dapan.bak")
        self.subs_var.set("exams/")
        self.config_var.set("configs/assignment_purchase_payment_ca3.yaml")
        self.test_data_var.set("test_data/")
        self._refresh_run_dir()
        if AVAILABLE_DRIVERS:
            self.driver_var.set(get_best_driver())

    def _refresh_run_dir(self):
        run_name = f"runs/run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.run_dir_var.set(run_name)

    def _browse_ans_bak(self):
        filename = filedialog.askopenfilename(
            title="Select Answer Database Backup",
            filetypes=[("Backup Files", "*.bak"), ("All Files", "*.*")]
        )
        if filename:
            self.ans_bak_var.set(os.path.relpath(filename, REPO_ROOT) if Path(filename).is_relative_to(REPO_ROOT) else filename)

    def _browse_subs(self):
        directory = filedialog.askdirectory(title="Select Submissions Folder")
        if directory:
            self.subs_var.set(os.path.relpath(directory, REPO_ROOT) if Path(directory).is_relative_to(REPO_ROOT) else directory)

    def _browse_config(self):
        filename = filedialog.askopenfilename(
            title="Select Config YAML File",
            filetypes=[("YAML Files", "*.yaml;*.yml"), ("All Files", "*.*")]
        )
        if filename:
            self.config_var.set(os.path.relpath(filename, REPO_ROOT) if Path(filename).is_relative_to(REPO_ROOT) else filename)

    def _browse_test_data(self):
        directory = filedialog.askdirectory(title="Select Test Data Folder")
        if directory:
            self.test_data_var.set(os.path.relpath(directory, REPO_ROOT) if Path(directory).is_relative_to(REPO_ROOT) else directory)

    def _browse_run_dir(self):
        directory = filedialog.askdirectory(title="Select Run Directory")
        if directory:
            self.run_dir_var.set(os.path.relpath(directory, REPO_ROOT) if Path(directory).is_relative_to(REPO_ROOT) else directory)

    def _on_auth_change(self, event=None):
        self._update_auth_fields_state()

    def _update_auth_fields_state(self):
        if self.auth_var.get() == "Windows Authentication":
            self.user_entry.config(state="disabled")
            self.pass_entry.config(state="disabled")
        else:
            self.user_entry.config(state="normal")
            self.pass_entry.config(state="normal")

    def _on_tree_select(self, event):
        selected = self.tree.selection()
        if selected:
            self.btn_open_reports.config(state="normal")
        else:
            self.btn_open_reports.config(state="disabled")

    # --- Logger functions ---

    def log(self, text: str):
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)

    def log_sanitized(self, text: str):
        pwd = self.pass_var.get()
        sanitized = sanitize_text(text, pwd)
        self.log(sanitized)

    # --- Pipeline and Process Control logic ---

    def _run_command_pipeline(self, pipeline_type: str):
        # 1. Validate inputs
        ans_bak = self.ans_bak_var.get()
        subs = self.subs_var.get()
        cfg = self.config_var.get()
        td = self.test_data_var.get()
        run_dir = self.run_dir_var.get()

        errors = validate_inputs(ans_bak, subs, cfg, td, run_dir, pipeline_type)
        if errors:
            error_msg = "\n".join(errors)
            messagebox.showerror("Validation Error", error_msg)
            return

        # 2. Build tasks queue
        self.pipeline_queue = []
        self.stop_requested = False

        if pipeline_type == "snapshot":
            self.pipeline_queue.append((
                build_snapshot_command(ans_bak, subs, run_dir, cfg),
                "Create Snapshot"
            ))
        elif pipeline_type == "compare-structure":
            self.pipeline_queue.append((
                build_compare_structure_command(run_dir, cfg),
                "Compare Structure"
            ))
        elif pipeline_type == "test-views":
            self.pipeline_queue.append((
                build_test_views_command(run_dir, td, cfg, ans_bak),
                "Test Views"
            ))
        elif pipeline_type == "full":
            self.pipeline_queue.append((
                build_snapshot_command(ans_bak, subs, run_dir, cfg),
                "Create Snapshot"
            ))
            self.pipeline_queue.append((
                build_compare_structure_command(run_dir, cfg),
                "Compare Structure"
            ))
            self.pipeline_queue.append((
                build_test_views_command(run_dir, td, cfg, ans_bak),
                "Test Views"
            ))

        # 3. Disable GUI actions and trigger thread
        for btn in self.all_action_buttons:
            btn.config(state="disabled")
        self.btn_stop.config(state="normal")

        self.log_text.delete("1.0", tk.END)
        self.log(f"=== Pipeline '{pipeline_type}' Started ===\n")
        self.log(f"Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

        self.running_thread = threading.Thread(target=self._pipeline_worker, daemon=True)
        self.running_thread.start()

    def _pipeline_worker(self):
        pipeline_failed = False
        start_time_total = time.time()

        # Build execution environment with SQL Server connection overrides
        env = os.environ.copy()
        env["PYTHONPATH"] = "src"
        env["DB_SERVER"] = self.server_var.get().strip()
        env["DB_DRIVER"] = self.driver_var.get().strip()
        
        if self.auth_var.get() == "SQL Server Authentication":
            env["DB_AUTH_MODE"] = "sql"
            env["DB_USER"] = self.user_var.get().strip()
            env["DB_PASSWORD"] = self.pass_var.get()
        else:
            env["DB_AUTH_MODE"] = "windows"
            
        env["DB_TRUST_CERT"] = "yes" if self.trust_var.get() else "no"

        while self.pipeline_queue and not self.stop_requested and not pipeline_failed:
            cmd, cmd_name = self.pipeline_queue.pop(0)
            
            self.log(f"--- Running stage: {cmd_name} ---\n")
            # Log exact command safely without printing secrets
            cmd_str_clean = " ".join(cmd)
            self.log(f"Command: {cmd_str_clean}\n")
            
            start_time = time.time()
            self.log(f"Stage Start: {datetime.now().strftime('%H:%M:%S')}\n")

            try:
                self.active_process = subprocess.Popen(
                    cmd,
                    cwd=str(REPO_ROOT),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1
                )
                
                # Stream logs in real-time
                while True:
                    line = self.active_process.stdout.readline()
                    if not line and self.active_process.poll() is not None:
                        break
                    if line:
                        self.log_sanitized(line)

                self.active_process.wait()
                exit_code = self.active_process.returncode
                elapsed = time.time() - start_time

                self.log(f"Stage Finish: {datetime.now().strftime('%H:%M:%S')}\n")
                self.log(f"Exit Code: {exit_code}\n")
                self.log(f"Elapsed Time: {elapsed:.2f}s\n\n")

                if exit_code != 0:
                    pipeline_failed = True
                    self.log(f"[ERROR] Stage '{cmd_name}' failed with exit code {exit_code}. Fail-fast active: aborting remaining stages.\n")

            except Exception as e:
                pipeline_failed = True
                self.log(f"[ERROR] Exception running stage '{cmd_name}': {e}\n\n")

        # Cleanup process reference
        self.active_process = None

        # Print final pipeline status
        elapsed_total = time.time() - start_time_total
        self.log(f"=== Pipeline Finished ===\n")
        self.log(f"End Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        self.log(f"Total Execution Time: {elapsed_total:.2f}s\n")
        
        if self.stop_requested:
            self.log("[STATUS] Pipeline was cancelled by user.\n")
        elif pipeline_failed:
            self.log("[STATUS] Pipeline completed with errors.\n")
        else:
            self.log("[STATUS] Pipeline completed successfully!\n")

        # Refresh preview and trigger UI re-enabling in main thread safely
        self.root.after(0, self._on_pipeline_finished)

    def _on_pipeline_finished(self):
        for btn in self.all_action_buttons:
            btn.config(state="normal")
        self.btn_stop.config(state="disabled")
        self._load_summary_preview()

    def _stop_process(self):
        if messagebox.askyesno("Cancel Pipeline", "Are you sure you want to stop the active command and cancel the pipeline?"):
            self.stop_requested = True
            self.pipeline_queue = []  # Clear remaining steps
            if self.active_process:
                try:
                    self.active_process.terminate()
                    self.log("\n[STATUS] Stop request sent to active process...\n")
                except Exception as e:
                    self.log(f"\n[ERROR] Failed to terminate active process: {e}\n")

    # --- Reports Preview and Path Navigation ---

    def _load_summary_preview(self):
        run_dir_str = self.run_dir_var.get().strip()
        if not run_dir_str:
            return
        
        run_dir = REPO_ROOT / run_dir_str
        summary_path = run_dir / "summary.csv"

        # Clear existing columns and items
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.tree["columns"] = []

        if not summary_path.exists():
            return

        try:
            df = pd.read_csv(summary_path)
            if df.empty:
                return

            columns = list(df.columns)
            self.tree["columns"] = columns

            # Bind column headings and set default widths
            for col in columns:
                self.tree.heading(col, text=col, anchor="w")
                self.tree.column(col, width=125, minwidth=80, stretch=True, anchor="w")

            # Insert data rows
            for _, row in df.iterrows():
                vals = ["" if pd.isna(val) else str(val) for val in row.values]
                self.tree.insert("", "end", values=vals)

        except Exception as e:
            self.log(f"[WARNING] Failed to load summary preview: {e}\n")

    def _safe_open_path(self, path: Path):
        if not path.exists():
            messagebox.showerror("Error", f"Path does not exist: {path}")
            return
        try:
            if hasattr(os, "startfile"):
                os.startfile(path)
            else:
                import subprocess
                if sys.platform == "darwin":
                    subprocess.run(["open", str(path)])
                else:
                    subprocess.run(["xdg-open", str(path)])
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open path: {e}")

    def _open_run_folder(self):
        run_dir_str = self.run_dir_var.get().strip()
        if not run_dir_str:
            messagebox.showerror("Error", "Run directory path is empty.")
            return
        run_path = REPO_ROOT / run_dir_str
        self._safe_open_path(run_path)

    def _open_summary_csv(self):
        run_dir_str = self.run_dir_var.get().strip()
        if not run_dir_str:
            messagebox.showerror("Error", "Run directory path is empty.")
            return
        summary_path = REPO_ROOT / run_dir_str / "summary.csv"
        self._safe_open_path(summary_path)

    def _open_mapping_reports(self):
        run_dir_str = self.run_dir_var.get().strip()
        if not run_dir_str:
            messagebox.showerror("Error", "Run directory path is empty.")
            return

        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Please select a student submission from the preview table.")
            return

        # Fetch student ID from the selected row. Let's assume submission_id is the first column.
        row_values = self.tree.item(selected[0], "values")
        if not row_values:
            return

        student_id = row_values[0]  # submission_id is column 0
        reports_dir = REPO_ROOT / run_dir_str / "submissions" / student_id / "reports"
        self._safe_open_path(reports_dir)

# --- Launcher Main Function ---

def main():
    root = tk.Tk()
    app = App(root)
    root.mainloop()
