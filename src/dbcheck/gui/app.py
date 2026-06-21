import os
import sys
import subprocess
import threading
import time
import csv
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

# Config import
from dbcheck.config import load_config

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
    command: str,
    execution_mode: str = "compare_seeded_test_data"
) -> List[str]:
    """
    Validates input paths based on the requested command and execution mode.
    Returns a list of error strings. If empty, validation passed.
    """
    errors = []
    
    # 1. Config path is required and must exist for all commands
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
    # For snapshot, test-views, or full pipeline, the answer backup must exist
    if command in ("snapshot", "test-views", "full"):
        ans_val = answer_bak.strip()
        if not ans_val:
            errors.append("Answer backup file (.bak) path is required.")
        else:
            ans_path = Path(ans_val)
            if not ans_path.is_absolute():
                ans_path = REPO_ROOT / ans_path
            if not ans_path.exists():
                errors.append(f"Answer backup file does not exist: {ans_val}")

    # For snapshot or full pipeline, the submissions folder must exist
    if command in ("snapshot", "full"):
        sub_val = submissions.strip()
        if not sub_val:
            errors.append("Student submissions folder path is required.")
        else:
            sub_path = Path(sub_val)
            if not sub_path.is_absolute():
                sub_path = REPO_ROOT / sub_path
            if not sub_path.exists():
                errors.append(f"Student submissions folder does not exist: {sub_val}")

    # Test Data is ONLY validated/required when execution_mode == "compare_seeded_test_data"
    if command in ("test-views", "full") and execution_mode == "compare_seeded_test_data":
        td_val = test_data.strip()
        if not td_val:
            errors.append("Test data folder path is required for compare_seeded_test_data mode.")
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

def build_test_views_command(run_dir: str, test_data: str, config: str, answer_bak: Optional[str] = None, execution_mode: str = "compare_seeded_test_data") -> List[str]:
    cmd = [
        sys.executable,
        "src/dbcheck/cli/main.py",
        "test-views",
        "--run-dir", str(Path(run_dir.strip())),
        "--config", str(Path(config.strip()))
    ]
    if execution_mode == "compare_seeded_test_data":
        if test_data and test_data.strip():
            cmd.extend(["--test-data", str(Path(test_data.strip()))])
    if answer_bak and answer_bak.strip():
        cmd.extend(["--answer-bak", str(Path(answer_bak.strip()))])
    return cmd

def build_export_results_command(run_dir: str, config: str) -> List[str]:
    return [
        sys.executable,
        "src/dbcheck/cli/main.py",
        "export-results",
        "--run-dir", str(Path(run_dir.strip())),
        "--config", str(Path(config.strip())),
        "--format", "xlsx"
    ]

def build_score_results_command(run_dir: str, config: str) -> List[str]:
    run_dir_path = Path(run_dir.strip())
    overrides_file = run_dir_path / "manual_overrides.csv"
    if not overrides_file.is_absolute():
        overrides_file = REPO_ROOT / overrides_file
        
    cmd = [
        sys.executable,
        "src/dbcheck/cli/main.py",
        "score-results",
        "--run-dir", str(Path(run_dir.strip())),
        "--config", str(Path(config.strip())),
        "--rubric", str(Path(run_dir.strip()) / "grading_rubric.csv")
    ]
    if overrides_file.exists():
        cmd.extend(["--overrides", str(Path(run_dir.strip()) / "manual_overrides.csv")])
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
        self.root.geometry("1100x900")
        self.root.minsize(1000, 800)

        # Process management variables
        self.active_process: Optional[subprocess.Popen] = None
        self.pipeline_queue: List[tuple] = []  # List of (command_list, name)
        self.stop_requested = False
        self.running_thread: Optional[threading.Thread] = None
        self.current_config_execution_mode = "compare_existing_data"

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
        self.root.rowconfigure(0, weight=0)  # Top 3-panel frame
        self.root.rowconfigure(1, weight=0)  # Pipeline Control panel
        self.root.rowconfigure(2, weight=1)  # Notebook section (expands)
        self.root.rowconfigure(3, weight=0)  # Bottom navigation frame

        # ----------------------------------------------------
        # Row 0: Top Horizontal 3-Panel Frame
        # ----------------------------------------------------
        top_frame = ttk.Frame(self.root)
        top_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=5)
        top_frame.columnconfigure(0, weight=3)
        top_frame.columnconfigure(1, weight=2)
        top_frame.columnconfigure(2, weight=2)

        # Panel 1: Input Paths (Column 0, weight 3)
        path_frame = ttk.LabelFrame(top_frame, text=" Input Paths ", padding=10)
        path_frame.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        path_frame.columnconfigure(1, weight=1)

        # Answer Backup
        ttk.Label(path_frame, text="Answer Backup:").grid(row=0, column=0, sticky="w", pady=2)
        self.ans_bak_var = tk.StringVar()
        ttk.Entry(path_frame, textvariable=self.ans_bak_var).grid(row=0, column=1, sticky="ew", padx=5, pady=2)
        ttk.Button(path_frame, text="Browse...", command=self._browse_ans_bak).grid(row=0, column=2, pady=2)

        # Student Submissions
        ttk.Label(path_frame, text="Submissions:").grid(row=1, column=0, sticky="w", pady=2)
        self.subs_var = tk.StringVar()
        ttk.Entry(path_frame, textvariable=self.subs_var).grid(row=1, column=1, sticky="ew", padx=5, pady=2)
        ttk.Button(path_frame, text="Browse...", command=self._browse_subs).grid(row=1, column=2, pady=2)

        # Config File
        ttk.Label(path_frame, text="Config:").grid(row=2, column=0, sticky="w", pady=2)
        self.config_var = tk.StringVar()
        ttk.Entry(path_frame, textvariable=self.config_var).grid(row=2, column=1, sticky="ew", padx=5, pady=2)
        ttk.Button(path_frame, text="Browse...", command=self._browse_config).grid(row=2, column=2, pady=2)

        # Test Data Folder
        ttk.Label(path_frame, text="Test Data:").grid(row=3, column=0, sticky="w", pady=2)
        self.test_data_var = tk.StringVar()
        self.test_data_entry = ttk.Entry(path_frame, textvariable=self.test_data_var)
        self.test_data_entry.grid(row=3, column=1, sticky="ew", padx=5, pady=2)
        self.btn_browse_test_data = ttk.Button(path_frame, text="Browse...", command=self._browse_test_data)
        self.btn_browse_test_data.grid(row=3, column=2, pady=2)

        # Run Directory
        ttk.Label(path_frame, text="Run Dir:").grid(row=4, column=0, sticky="w", pady=2)
        self.run_dir_var = tk.StringVar()
        run_dir_subframe = ttk.Frame(path_frame)
        run_dir_subframe.grid(row=4, column=1, sticky="ew", padx=5, pady=2)
        run_dir_subframe.columnconfigure(0, weight=1)
        ttk.Entry(run_dir_subframe, textvariable=self.run_dir_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(run_dir_subframe, text="🔄 Refresh", command=self._refresh_run_dir).grid(row=0, column=1, padx=(5, 0))
        ttk.Button(path_frame, text="Browse...", command=self._browse_run_dir).grid(row=4, column=2, pady=2)

        # Panel 2: Selected Config Summary (Column 1, weight 2)
        config_summary_frame = ttk.LabelFrame(top_frame, text=" Selected Config Summary ", padding=10)
        config_summary_frame.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)
        config_summary_frame.columnconfigure(1, weight=1)

        labels_cfg = [
            ("Assignment:", "lbl_cfg_name"),
            ("Views Mode:", "lbl_views_mode"),
            ("Execution Mode:", "lbl_exec_mode"),
            ("Export Outputs:", "lbl_export_out"),
            ("Multiset Compare:", "lbl_multiset"),
            ("Key Grading Mode:", "lbl_key_mode"),
            ("Allow Surrogate Keys:", "lbl_allow_surr"),
            ("Allow Natural Keys:", "lbl_allow_nat")
        ]
        for idx, (label_text, attr_name) in enumerate(labels_cfg):
            ttk.Label(config_summary_frame, text=label_text).grid(row=idx, column=0, sticky="w", pady=1)
            lbl_val = ttk.Label(config_summary_frame, text="-", font=("Segoe UI", 9, "bold") if idx == 0 else ("Segoe UI", 9))
            lbl_val.grid(row=idx, column=1, sticky="w", padx=5, pady=1)
            setattr(self, attr_name, lbl_val)

        self.lbl_config_note = ttk.Label(config_summary_frame, font=("Segoe UI", 8, "italic"), foreground="#005999", wraplength=250, justify="left", text="")
        self.lbl_config_note.grid(row=8, column=0, columnspan=2, sticky="w", pady=(5, 0))

        # Panel 3: SQL Server Connection Settings (Column 2, weight 2)
        sql_frame = ttk.LabelFrame(top_frame, text=" SQL Server Settings ", padding=10)
        sql_frame.grid(row=0, column=2, sticky="nsew", padx=5, pady=5)
        sql_frame.columnconfigure(1, weight=1)

        # Server name
        ttk.Label(sql_frame, text="Server Name:").grid(row=0, column=0, sticky="w", pady=2)
        self.server_var = tk.StringVar(value=".")
        ttk.Entry(sql_frame, textvariable=self.server_var).grid(row=0, column=1, sticky="ew", padx=5, pady=2)

        # Driver
        ttk.Label(sql_frame, text="Driver:").grid(row=1, column=0, sticky="w", pady=2)
        self.driver_var = tk.StringVar()
        self.driver_combo = ttk.Combobox(sql_frame, textvariable=self.driver_var, values=AVAILABLE_DRIVERS)
        self.driver_combo.grid(row=1, column=1, sticky="ew", padx=5, pady=2)
        if not AVAILABLE_DRIVERS:
            self.driver_var.set("ODBC Driver 17 for SQL Server")
            self.log("[WARNING] Could not discover SQL Server drivers via pyodbc. Driver field remains editable.\n")

        # Authentication mode
        ttk.Label(sql_frame, text="Authentication:").grid(row=2, column=0, sticky="w", pady=2)
        self.auth_var = tk.StringVar(value="Windows Authentication")
        self.auth_combo = ttk.Combobox(
            sql_frame, 
            textvariable=self.auth_var, 
            values=["Windows Authentication", "SQL Server Authentication"], 
            state="readonly"
        )
        self.auth_combo.grid(row=2, column=1, sticky="ew", padx=5, pady=2)
        self.auth_combo.bind("<<ComboboxSelected>>", self._on_auth_change)

        # Username
        self.user_label = ttk.Label(sql_frame, text="Username:")
        self.user_label.grid(row=3, column=0, sticky="w", pady=2)
        self.user_var = tk.StringVar()
        self.user_entry = ttk.Entry(sql_frame, textvariable=self.user_var)
        self.user_entry.grid(row=3, column=1, sticky="ew", padx=5, pady=2)

        # Password
        self.pass_label = ttk.Label(sql_frame, text="Password:")
        self.pass_label.grid(row=4, column=0, sticky="w", pady=2)
        self.pass_var = tk.StringVar()
        self.pass_entry = ttk.Entry(sql_frame, textvariable=self.pass_var, show="*")
        self.pass_entry.grid(row=4, column=1, sticky="ew", padx=5, pady=2)

        # Trust Cert checkbox
        self.trust_var = tk.BooleanVar(value=True)
        self.trust_check = ttk.Checkbutton(sql_frame, text="Trust Server Certificate (Encrypt=No)", variable=self.trust_var)
        self.trust_check.grid(row=5, column=0, columnspan=2, sticky="w", pady=2)

        self._update_auth_fields_state()

        # ----------------------------------------------------
        # Row 1: Pipeline Execution Control Panel
        # ----------------------------------------------------
        control_frame = ttk.LabelFrame(self.root, text=" Pipeline Execution Control ", padding=10)
        control_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
        control_frame.columnconfigure(1, weight=1)

        # Row 0: Action Buttons
        buttons_frame = ttk.Frame(control_frame)
        buttons_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 5))

        self.btn_snap = ttk.Button(buttons_frame, text="Run Snapshot", command=lambda: self._run_command_pipeline("snapshot"))
        self.btn_snap.grid(row=0, column=0, padx=5)

        self.btn_comp = ttk.Button(buttons_frame, text="Compare Structure", command=lambda: self._run_command_pipeline("compare-structure"))
        self.btn_comp.grid(row=0, column=1, padx=5)

        self.btn_views = ttk.Button(buttons_frame, text="Test Views", command=lambda: self._run_command_pipeline("test-views"))
        self.btn_views.grid(row=0, column=2, padx=5)

        self.btn_export = ttk.Button(buttons_frame, text="Export Results", command=lambda: self._run_command_pipeline("export-results"))
        self.btn_export.grid(row=0, column=3, padx=5)

        self.btn_full = ttk.Button(buttons_frame, text="Run Full Pipeline", style="Primary.TButton", command=lambda: self._run_command_pipeline("full"))
        self.btn_full.grid(row=0, column=4, padx=5)

        self.run_scoring_var = tk.BooleanVar(value=False)
        self.chk_run_scoring = ttk.Checkbutton(buttons_frame, text="Run scoring after export", variable=self.run_scoring_var)
        self.chk_run_scoring.grid(row=0, column=5, padx=5)

        self.btn_stop = ttk.Button(buttons_frame, text="Stop", style="Stop.TButton", command=self._stop_process)
        self.btn_stop.grid(row=0, column=6, padx=5)
        self.btn_stop.config(state="disabled")

        self.btn_refresh = ttk.Button(buttons_frame, text="Refresh Results", command=self._on_refresh_clicked)
        self.btn_refresh.grid(row=0, column=7, padx=5)

        self.all_action_buttons = [self.btn_snap, self.btn_comp, self.btn_views, self.btn_export, self.btn_full, self.btn_refresh]

        # Row 1: Progress elements
        progress_frame = ttk.Frame(control_frame)
        progress_frame.grid(row=1, column=0, columnspan=2, sticky="ew")
        progress_frame.columnconfigure(2, weight=1)

        self.progress_bar = ttk.Progressbar(progress_frame, orient="horizontal", mode="determinate", length=300)
        self.progress_bar.grid(row=0, column=0, padx=5, sticky="w")

        self.lbl_step_counter = ttk.Label(progress_frame, text="Step 0/4", font=("Segoe UI", 9, "bold"))
        self.lbl_step_counter.grid(row=0, column=1, padx=10, sticky="w")

        self.lbl_step_name = ttk.Label(progress_frame, text="Idle", font=("Segoe UI", 9))
        self.lbl_step_name.grid(row=0, column=2, padx=10, sticky="w")

        self.lbl_last_status = ttk.Label(progress_frame, text="Status: Ready", font=("Segoe UI", 9, "bold"))
        self.lbl_last_status.grid(row=0, column=3, padx=10, sticky="e")

        # ----------------------------------------------------
        # Row 2: Results Notebook
        # ----------------------------------------------------
        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=2, column=0, sticky="nsew", padx=10, pady=5)

        # Tab 1: Grading Summary Table
        tab_summary = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(tab_summary, text=" Grading Summary Table ")
        tab_summary.columnconfigure(0, weight=1)
        tab_summary.rowconfigure(0, weight=1)

        tree_scroll_y = ttk.Scrollbar(tab_summary, orient="vertical")
        tree_scroll_x = ttk.Scrollbar(tab_summary, orient="horizontal")
        self.tree = ttk.Treeview(
            tab_summary, 
            yscrollcommand=tree_scroll_y.set, 
            xscrollcommand=tree_scroll_x.set, 
            selectmode="browse"
        )
        tree_scroll_y.config(command=self.tree.yview)
        tree_scroll_x.config(command=self.tree.xview)
        self.tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll_y.grid(row=0, column=1, sticky="ns")
        tree_scroll_x.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        # Tab 2: Metrics Dashboard
        tab_dash = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(tab_dash, text=" Metrics Dashboard ")
        
        dash_canvas = tk.Canvas(tab_dash, borderwidth=0, highlightthickness=0)
        dash_scroll = ttk.Scrollbar(tab_dash, orient="vertical", command=dash_canvas.yview)
        self.dash_frame = ttk.Frame(dash_canvas, padding=10)
        self.dash_frame.bind(
            "<Configure>",
            lambda e: dash_canvas.configure(scrollregion=dash_canvas.bbox("all"))
        )
        dash_canvas.create_window((0, 0), window=self.dash_frame, anchor="nw")
        dash_canvas.configure(yscrollcommand=dash_scroll.set)
        dash_canvas.pack(side="left", fill="both", expand=True)
        dash_scroll.pack(side="right", fill="y")

        # Tab 3: Issues & Review Queue
        tab_rq = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(tab_rq, text=" Issues & Review Queue ")
        tab_rq.columnconfigure(0, weight=1)
        tab_rq.rowconfigure(1, weight=1)

        filter_frame = ttk.Frame(tab_rq, padding=5)
        filter_frame.grid(row=0, column=0, sticky="ew")
        
        # Row 0 filters
        ttk.Label(filter_frame, text="Category:").grid(row=0, column=0, sticky="w", padx=2, pady=2)
        self.rq_filter_var = tk.StringVar(value="All Items")
        self.rq_filter_combo = ttk.Combobox(
            filter_frame, 
            textvariable=self.rq_filter_var,
            values=["All Items", "Hard Errors Only", "Review Required Only", "View Issues Only", "PK/FK Issues Only", "Mapping Issues Only"],
            state="readonly",
            width=20
        )
        self.rq_filter_combo.grid(row=0, column=1, sticky="w", padx=2, pady=2)
        self.rq_filter_combo.bind("<<ComboboxSelected>>", self._on_filter_changed)
        
        ttk.Label(filter_frame, text="Submission ID:").grid(row=0, column=2, sticky="w", padx=5, pady=2)
        self.rq_sub_id_var = tk.StringVar()
        self.rq_sub_id_entry = ttk.Entry(filter_frame, textvariable=self.rq_sub_id_var, width=15)
        self.rq_sub_id_entry.grid(row=0, column=3, sticky="w", padx=2, pady=2)
        self.rq_sub_id_var.trace_add("write", self._on_filter_changed)
        
        ttk.Label(filter_frame, text="Source Report:").grid(row=0, column=4, sticky="w", padx=5, pady=2)
        self.rq_report_var = tk.StringVar()
        self.rq_report_entry = ttk.Entry(filter_frame, textvariable=self.rq_report_var, width=20)
        self.rq_report_entry.grid(row=0, column=5, sticky="w", padx=2, pady=2)
        self.rq_report_var.trace_add("write", self._on_filter_changed)

        # Row 1 filters
        ttk.Label(filter_frame, text="Component:").grid(row=1, column=0, sticky="w", padx=2, pady=2)
        self.rq_component_var = tk.StringVar()
        self.rq_component_entry = ttk.Entry(filter_frame, textvariable=self.rq_component_var, width=20)
        self.rq_component_entry.grid(row=1, column=1, sticky="w", padx=2, pady=2)
        self.rq_component_var.trace_add("write", self._on_filter_changed)

        ttk.Label(filter_frame, text="Status:").grid(row=1, column=2, sticky="w", padx=5, pady=2)
        self.rq_status_var = tk.StringVar()
        self.rq_status_entry = ttk.Entry(filter_frame, textvariable=self.rq_status_var, width=15)
        self.rq_status_entry.grid(row=1, column=3, sticky="w", padx=2, pady=2)
        self.rq_status_var.trace_add("write", self._on_filter_changed)

        ttk.Label(filter_frame, text="Severity:").grid(row=1, column=4, sticky="w", padx=5, pady=2)
        self.rq_severity_var = tk.StringVar()
        self.rq_severity_entry = ttk.Entry(filter_frame, textvariable=self.rq_severity_var, width=20)
        self.rq_severity_entry.grid(row=1, column=5, sticky="w", padx=2, pady=2)
        self.rq_severity_var.trace_add("write", self._on_filter_changed)
        
        btn_clear_filters = ttk.Button(filter_frame, text="Clear Filters", command=self._clear_rq_filters)
        btn_clear_filters.grid(row=1, column=6, padx=10, pady=2)

        rq_scroll_y = ttk.Scrollbar(tab_rq, orient="vertical")
        rq_scroll_x = ttk.Scrollbar(tab_rq, orient="horizontal")
        self.rq_tree = ttk.Treeview(
            tab_rq,
            yscrollcommand=rq_scroll_y.set,
            xscrollcommand=rq_scroll_x.set,
            selectmode="browse"
        )
        rq_scroll_y.config(command=self.rq_tree.yview)
        rq_scroll_x.config(command=self.rq_tree.xview)
        self.rq_tree.grid(row=1, column=0, sticky="nsew")
        rq_scroll_y.grid(row=1, column=1, sticky="ns")
        rq_scroll_x.grid(row=2, column=0, sticky="ew")
        
        rq_cols = ["Submission ID", "Category", "Source Report", "Component", "Status", "Severity", "Message", "Suggested Action", "Evidence"]
        self.rq_tree["columns"] = rq_cols
        self.rq_tree.column("#0", width=0, stretch=False)
        for col in rq_cols:
            self.rq_tree.heading(col, text=col, anchor="w")
            self.rq_tree.column(col, width=120, minwidth=60, stretch=True, anchor="w")

        # Tab 4: Scoring / Rubric
        tab_scoring = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab_scoring, text=" Scoring / Rubric ")
        
        # 1. Top action buttons bar
        scoring_actions = ttk.Frame(tab_scoring)
        scoring_actions.pack(fill="x", pady=(0, 10))
        
        ttk.Button(scoring_actions, text="Load Rubric", command=self._load_rubric_file).pack(side="left", padx=5)
        ttk.Button(scoring_actions, text="Save Rubric", command=self._save_rubric).pack(side="left", padx=5)
        self.btn_score = ttk.Button(scoring_actions, text="Score Results", style="Primary.TButton", command=lambda: self._run_command_pipeline("score-results"))
        self.btn_score.pack(side="left", padx=5)
        ttk.Button(scoring_actions, text="Open grading_summary.xlsx", command=self._open_grading_summary_xlsx).pack(side="left", padx=5)
        
        # 2. Main content area
        scoring_main = ttk.Frame(tab_scoring)
        scoring_main.pack(fill="both", expand=True)
        scoring_main.columnconfigure(0, weight=1)
        scoring_main.columnconfigure(1, weight=1)
        
        # Left Frame: Standard Components
        std_frame = ttk.LabelFrame(scoring_main, text=" Standard Components ", padding=10)
        std_frame.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        std_frame.columnconfigure(1, weight=1)
        
        self.pt_tables = tk.StringVar(value="1.0")
        self.pt_columns = tk.StringVar(value="2.0")
        self.pt_pks = tk.StringVar(value="1.0")
        self.pt_fks = tk.StringVar(value="1.0")
        self.pt_row_counts = tk.StringVar(value="1.0")
        self.pt_manual = tk.StringVar(value="0.0")
        
        self.pt_tables.trace_add("write", self._update_rubric_total)
        self.pt_columns.trace_add("write", self._update_rubric_total)
        self.pt_pks.trace_add("write", self._update_rubric_total)
        self.pt_fks.trace_add("write", self._update_rubric_total)
        self.pt_row_counts.trace_add("write", self._update_rubric_total)
        self.pt_manual.trace_add("write", self._update_rubric_total)
        
        ttk.Label(std_frame, text="Tables Point Allocation:").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(std_frame, textvariable=self.pt_tables, width=10).grid(row=0, column=1, sticky="w", padx=5, pady=5)
        
        ttk.Label(std_frame, text="Columns Point Allocation:").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(std_frame, textvariable=self.pt_columns, width=10).grid(row=1, column=1, sticky="w", padx=5, pady=5)
        
        ttk.Label(std_frame, text="Primary Keys Point Allocation:").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(std_frame, textvariable=self.pt_pks, width=10).grid(row=2, column=1, sticky="w", padx=5, pady=5)
        
        ttk.Label(std_frame, text="Foreign Keys Point Allocation:").grid(row=3, column=0, sticky="w", pady=5)
        ttk.Entry(std_frame, textvariable=self.pt_fks, width=10).grid(row=3, column=1, sticky="w", padx=5, pady=5)
        
        ttk.Label(std_frame, text="Row Counts Point Allocation:").grid(row=4, column=0, sticky="w", pady=5)
        ttk.Entry(std_frame, textvariable=self.pt_row_counts, width=10).grid(row=4, column=1, sticky="w", padx=5, pady=5)
        
        ttk.Label(std_frame, text="Manual Section Point Allocation:").grid(row=5, column=0, sticky="w", pady=5)
        ttk.Entry(std_frame, textvariable=self.pt_manual, width=10).grid(row=5, column=1, sticky="w", padx=5, pady=5)
        
        # Right Frame: Views / Queries
        self.rubric_views_frame = ttk.LabelFrame(scoring_main, text=" Views / Queries ", padding=10)
        self.rubric_views_frame.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)
        self.rubric_views_frame.columnconfigure(1, weight=1)
        self.pt_views = {}
        
        # 3. Bottom status and warning area
        scoring_bottom = ttk.Frame(tab_scoring)
        scoring_bottom.pack(fill="x", pady=(10, 0))
        
        self.lbl_rubric_total = ttk.Label(scoring_bottom, text="Total Rubric Points: 0.00", font=("Segoe UI", 11, "bold"))
        self.lbl_rubric_total.pack(side="left", padx=5)
        
        self.lbl_rubric_warning = ttk.Label(scoring_bottom, text="", font=("Segoe UI", 10, "italic"))
        self.lbl_rubric_warning.pack(side="left", padx=20)
        
        # Tab 5: Results Files
        tab_files = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab_files, text=" Results Files ")
        
        self.files_list = [
            ("summary.xlsx", "summary.xlsx"),
            ("summary.csv", "summary.csv"),
            ("grading_summary.xlsx", "grading_summary.xlsx"),
            ("grading_summary.csv", "grading_summary.csv"),
            ("grading_detail.csv", "grading_detail.csv"),
            ("grading_rubric.csv", "grading_rubric.csv"),
            ("review_queue.xlsx", "review_queue.xlsx"),
            ("review_queue.csv", "review_queue.csv"),
            ("hard_errors.csv", "hard_errors.csv"),
            ("execution.log", "execution.log"),
            ("student_feedback Folder", "student_feedback"),
            # Submission-dependent view SQL folders (resolved from selected student)
            ("view_sql Folder", "view_sql:view_sql"),
            ("view_sql/raw Folder", "view_sql:view_sql/raw"),
            ("view_sql/rewritten Folder", "view_sql:view_sql/rewritten"),
            ("view_sql/diff Folder", "view_sql:view_sql/diff"),
            ("Run Directory Root", "")
        ]
        self.file_widgets = []
        for name, subpath in self.files_list:
            row_frame = ttk.Frame(tab_files, padding=5)
            row_frame.pack(fill="x", pady=5)
            
            lbl_name = ttk.Label(row_frame, text=name, font=("Segoe UI", 10, "bold"), width=25)
            lbl_name.pack(side="left", padx=5)
            
            lbl_status = ttk.Label(row_frame, text="[Not generated]", foreground="gray")
            lbl_status.pack(side="left", padx=10)
            
            btn_open = ttk.Button(row_frame, text="Open")
            btn_open.pack(side="right", padx=5)
            
            self.file_widgets.append((lbl_status, btn_open, subpath))

        self.all_action_buttons.append(self.btn_score)

        # Tab 5: Console / Logs
        tab_logs = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(tab_logs, text=" Console / Logs ")
        tab_logs.columnconfigure(0, weight=1)
        tab_logs.rowconfigure(0, weight=1)
        
        self.log_text = scrolledtext.ScrolledText(tab_logs, font=("Consolas", 9), bg="#fafafa")
        self.log_text.grid(row=0, column=0, sticky="nsew")

        # ----------------------------------------------------
        # Row 3: Bottom Navigation Buttons Frame
        # ----------------------------------------------------
        nav_frame = ttk.Frame(self.root, padding=5)
        nav_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=5)

        self.btn_open_run = ttk.Button(nav_frame, text="Open Run Folder", command=self._open_run_folder)
        self.btn_open_run.pack(side="left", padx=5)

        self.btn_open_summary = ttk.Button(nav_frame, text="Open Summary CSV", command=self._open_summary_csv)
        self.btn_open_summary.pack(side="left", padx=5)

        self.btn_open_reports = ttk.Button(nav_frame, text="Open Mapping Reports", command=self._open_mapping_reports)
        self.btn_open_reports.pack(side="left", padx=5)
        self.btn_open_reports.config(state="disabled")

    # --- Defaults and Browse Handlers ---

    def _load_defaults(self):
        self.ans_bak_var.set("solution/dapan.bak")
        self.subs_var.set("exams/")
        self.config_var.set("configs/assignment_purchase_payment_ca3.yaml")
        self.test_data_var.set("test_data/")
        self._refresh_run_dir()
        if AVAILABLE_DRIVERS:
            self.driver_var.set(get_best_driver())
            
        # Bind tracer to self.config_var to track configurations
        self.config_var.trace_add("write", self._on_config_changed)
        self._on_config_changed()

    def _on_config_changed(self, *args):
        cfg_path_str = self.config_var.get().strip()
        if not cfg_path_str:
            self._clear_config_summary()
            return
            
        cfg_path = Path(cfg_path_str)
        if not cfg_path.is_absolute():
            cfg_path = REPO_ROOT / cfg_path
            
        if not cfg_path.exists():
            self._clear_config_summary()
            return
            
        try:
            config = load_config(str(cfg_path))
            self.lbl_cfg_name.config(text=config.name)
            self.lbl_views_mode.config(text=config.views_mode)
            self.lbl_exec_mode.config(text=config.execution_mode)
            self.lbl_export_out.config(text=str(config.export_outputs))
            self.lbl_multiset.config(text=str(config.compare_as_multiset))
            
            kg = getattr(config.schema, "key_grading", None)
            if kg:
                self.lbl_key_mode.config(text=kg.mode)
                self.lbl_allow_surr.config(text=str(kg.allow_surrogate_keys))
                self.lbl_allow_nat.config(text=str(kg.allow_natural_keys))
            else:
                self.lbl_key_mode.config(text="n/a")
                self.lbl_allow_surr.config(text="n/a")
                self.lbl_allow_nat.config(text="n/a")
                
            self.current_config_execution_mode = config.execution_mode
            
            if config.execution_mode == "compare_rewritten_sql_on_answer_db":
                self.test_data_entry.config(state="disabled")
                self.btn_browse_test_data.config(state="disabled")
                self.lbl_config_note.config(text="Student view SQL is rewritten using table/column mappings and executed on the answer database. View names are not used for grading.")
            elif config.execution_mode == "compare_existing_data":
                self.test_data_entry.config(state="disabled")
                self.btn_browse_test_data.config(state="disabled")
                self.lbl_config_note.config(text="View testing compares existing restored .bak data; no seeding.")
            else:
                self.test_data_entry.config(state="normal")
                self.btn_browse_test_data.config(state="normal")
                self.lbl_config_note.config(text="View testing seeds test data; Test Data Folder is required.")
                
            # Clear views first
            for widget in self.rubric_views_frame.winfo_children():
                widget.destroy()
            self.pt_views = {}
            
            # Redraw view inputs dynamically
            for i, view_cfg in enumerate(config.views):
                v_name = view_cfg.answer_view
                ttk.Label(self.rubric_views_frame, text=f"View {v_name}:").grid(row=i, column=0, sticky="w", pady=2)
                var = tk.StringVar(value="1.0")
                var.trace_add("write", self._update_rubric_total)
                self.pt_views[v_name] = var
                ttk.Entry(self.rubric_views_frame, textvariable=var, width=10).grid(row=i, column=1, sticky="w", padx=5, pady=2)
            self._update_rubric_total()
            
        except Exception as e:
            self._clear_config_summary()
            self.log(f"[WARNING] Failed to parse config properties: {e}\n")

    def _clear_config_summary(self):
        self.lbl_cfg_name.config(text="-")
        self.lbl_views_mode.config(text="-")
        self.lbl_exec_mode.config(text="-")
        self.lbl_export_out.config(text="-")
        self.lbl_multiset.config(text="-")
        self.lbl_key_mode.config(text="-")
        self.lbl_allow_surr.config(text="-")
        self.lbl_allow_nat.config(text="-")
        self.lbl_config_note.config(text="")
        self.current_config_execution_mode = "compare_existing_data"
        self.test_data_entry.config(state="normal")
        self.btn_browse_test_data.config(state="normal")
        
        # Clear rubric views frame
        if hasattr(self, 'rubric_views_frame'):
            for widget in self.rubric_views_frame.winfo_children():
                widget.destroy()
        self.pt_views = {}
        self._update_rubric_total()

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
        # Refresh files tab to update submission-dependent view_sql folder buttons
        self._load_results_files_status()

    def _on_filter_changed(self, event=None):
        self._load_review_queue_data()

    def _clear_rq_filters(self):
        self.rq_filter_var.set("All Items")
        self.rq_sub_id_var.set("")
        self.rq_report_var.set("")
        self.rq_component_var.set("")
        self.rq_status_var.set("")
        self.rq_severity_var.set("")
        self._load_review_queue_data()

    # --- Logger functions ---

    def log(self, text: str):
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)

    def log_sanitized(self, text: str):
        pwd = self.pass_var.get()
        sanitized = sanitize_text(text, pwd)
        self.log(sanitized)

    # --- Pipeline and Process Control logic ---

    def _update_progress_ui(self, progress_val=None, step_counter_text=None, step_name_text=None, last_status_text=None):
        if progress_val is not None:
            self.progress_bar["value"] = progress_val
        if step_counter_text is not None:
            self.lbl_step_counter.config(text=step_counter_text)
        if step_name_text is not None:
            self.lbl_step_name.config(text=step_name_text)
        if last_status_text is not None:
            self.lbl_last_status.config(text=last_status_text)
            if "Failed" in last_status_text or "Error" in last_status_text:
                self.lbl_last_status.config(foreground="red")
            elif "Stopped" in last_status_text:
                self.lbl_last_status.config(foreground="orange")
            elif "Succeeded" in last_status_text or "Successfully" in last_status_text:
                self.lbl_last_status.config(foreground="green")
            else:
                self.lbl_last_status.config(foreground="black")

    def _on_refresh_clicked(self):
        self._load_summary_preview()

    def _run_command_pipeline(self, pipeline_type: str):
        ans_bak = self.ans_bak_var.get()
        subs = self.subs_var.get()
        cfg = self.config_var.get()
        td = self.test_data_var.get()
        run_dir = self.run_dir_var.get()
        exec_mode = self.current_config_execution_mode

        errors = validate_inputs(ans_bak, subs, cfg, td, run_dir, pipeline_type, exec_mode)
        if errors:
            error_msg = "\n".join(errors)
            messagebox.showerror("Validation Error", error_msg)
            return

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
                build_test_views_command(run_dir, td, cfg, ans_bak, exec_mode),
                "Test Views"
            ))
        elif pipeline_type == "export-results":
            self.pipeline_queue.append((
                build_export_results_command(run_dir, cfg),
                "Export Results"
            ))
        elif pipeline_type == "score-results":
            rubric_file = REPO_ROOT / Path(run_dir.strip()) / "grading_rubric.csv"
            if not rubric_file.exists():
                self._save_rubric()
            self.pipeline_queue.append((
                build_score_results_command(run_dir, cfg),
                "Score Results"
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
                build_test_views_command(run_dir, td, cfg, ans_bak, exec_mode),
                "Test Views"
            ))
            self.pipeline_queue.append((
                build_export_results_command(run_dir, cfg),
                "Export Results"
            ))
            # Append score-results if checked or rubric file already exists
            if self.run_scoring_var.get() or (REPO_ROOT / Path(run_dir.strip()) / "grading_rubric.csv").exists():
                if self.run_scoring_var.get() and not (REPO_ROOT / Path(run_dir.strip()) / "grading_rubric.csv").exists():
                    self._save_rubric()
                self.pipeline_queue.append((
                    build_score_results_command(run_dir, cfg),
                    "Score Results"
                ))

        # Disable GUI buttons during execution
        for btn in self.all_action_buttons:
            btn.config(state="disabled")
        self.btn_stop.config(state="normal")

        # Auto-switch to Console / Logs tab (index 5)
        self.notebook.select(5)

        self.log_text.delete("1.0", tk.END)
        self.log(f"=== Pipeline '{pipeline_type}' Started ===\n")
        self.log(f"Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

        self.running_thread = threading.Thread(target=self._pipeline_worker, kwargs={"run_dir_str": run_dir}, daemon=True)
        self.running_thread.start()

    def _tail_execution_log(self, run_dir_str: str):
        log_path = REPO_ROOT / run_dir_str / "execution.log"
        if log_path.exists():
            self.log(f"\n--- Tail of execution.log ({log_path.name}) ---\n")
            try:
                with open(log_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    tail_lines = lines[-50:]
                    for line in tail_lines:
                        self.log(line)
            except Exception as e:
                self.log(f"[WARNING] Failed to tail execution.log: {e}\n")
            self.log("------------------------------------------\n\n")

    def _pipeline_worker(self, run_dir_str: str):
        pipeline_failed = False
        start_time_total = time.time()

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

        total_steps = len(self.pipeline_queue)
        current_step_idx = 0
        completed_stages = []

        self.root.after(0, lambda: self._update_progress_ui(0, f"Step 0/{total_steps}", "Starting...", "Status: Running"))

        while self.pipeline_queue and not self.stop_requested and not pipeline_failed:
            cmd, cmd_name = self.pipeline_queue.pop(0)
            current_step_idx += 1
            
            step_text = f"Step {current_step_idx}/{total_steps}: {cmd_name}"
            run_text = f"Running {cmd_name}..."
            self.root.after(0, lambda st=step_text, rt=run_text: self._update_progress_ui(None, st, rt, "Status: Running"))

            self.log(f"--- Running stage: {cmd_name} ---\n")
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

                # Tail log after stage completes
                self._tail_execution_log(run_dir_str)

                if exit_code != 0:
                    pipeline_failed = True
                    self.log(f"[ERROR] Stage '{cmd_name}' failed with exit code {exit_code}. Fail-fast active: aborting remaining stages.\n")
                    self.root.after(0, lambda cn=cmd_name: self.lbl_last_status.config(text=f"Status: {cn} Failed", foreground="red"))
                else:
                    completed_stages.append(cmd_name)
                    if total_steps == 4:  # Full Pipeline
                        if cmd_name == "Create Snapshot":
                            progress_val = 25
                        elif cmd_name == "Compare Structure":
                            progress_val = 50
                        elif cmd_name == "Test Views":
                            progress_val = 75
                        elif cmd_name == "Export Results":
                            progress_val = 100
                        else:
                            progress_val = int(100 * (current_step_idx / total_steps))
                    elif total_steps == 5:  # Full Pipeline with scoring
                        if cmd_name == "Create Snapshot":
                            progress_val = 20
                        elif cmd_name == "Compare Structure":
                            progress_val = 40
                        elif cmd_name == "Test Views":
                            progress_val = 60
                        elif cmd_name == "Export Results":
                            progress_val = 80
                        elif cmd_name == "Score Results":
                            progress_val = 100
                        else:
                            progress_val = int(100 * (current_step_idx / total_steps))
                    else:
                        progress_val = int(100 * (current_step_idx / total_steps))
                        
                    status_text = f"Status: {cmd_name} Succeeded"
                    self.root.after(0, lambda val=progress_val, st=status_text: self._update_progress_ui(val, None, None, st))

            except Exception as e:
                pipeline_failed = True
                self.log(f"[ERROR] Exception running stage '{cmd_name}': {e}\n\n")
                self.root.after(0, lambda cn=cmd_name: self.lbl_last_status.config(text=f"Status: {cn} Error", foreground="red"))

        self.active_process = None
        elapsed_total = time.time() - start_time_total
        
        self.log(f"=== Pipeline Finished ===\n")
        self.log(f"End Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        self.log(f"Total Execution Time: {elapsed_total:.2f}s\n")
        
        if self.stop_requested:
            self.log("[STATUS] Pipeline was cancelled by user.\n")
            status_lbl = "Status: Stopped by user"
            self.root.after(0, lambda: self._update_progress_ui(None, "Step 0/0", "Stopped by user", status_lbl))
        elif pipeline_failed:
            self.log("[STATUS] Pipeline completed with errors.\n")
            status_lbl = "Status: Failed"
            self.root.after(0, lambda: self._update_progress_ui(None, None, "Failed", status_lbl))
        else:
            self.log("[STATUS] Pipeline completed successfully!\n")
            status_lbl = "Status: Completed Successfully"
            self.root.after(0, lambda: self._update_progress_ui(100, None, "Success", status_lbl))

        # Refresh metrics only if Export Results or Score Results succeeded and the pipeline was NOT cancelled.
        should_refresh = (("Export Results" in completed_stages) or ("Score Results" in completed_stages)) and not self.stop_requested
        self.root.after(0, lambda sr=should_refresh: self._on_pipeline_finished(sr))

    def _on_pipeline_finished(self, should_refresh: bool):
        for btn in self.all_action_buttons:
            btn.config(state="normal")
        self.btn_stop.config(state="disabled")
        if should_refresh:
            self._load_summary_preview()
        else:
            self._load_results_files_status()

    def _stop_process(self):
        if messagebox.askyesno("Cancel Pipeline", "Are you sure you want to stop the active command and cancel the pipeline?"):
            self.stop_requested = True
            self.pipeline_queue = []
            if self.active_process:
                try:
                    self.active_process.terminate()
                    self.log("\n[STATUS] Stop request sent to active process...\n")
                except Exception as e:
                    self.log(f"\n[ERROR] Failed to terminate active process: {e}\n")

    # --- Reports Preview and Path Navigation ---

    def _load_summary_dataframe(self) -> pd.DataFrame:
        run_dir_str = self.run_dir_var.get().strip()
        if not run_dir_str:
            return pd.DataFrame()
            
        run_dir = REPO_ROOT / run_dir_str
        grading_csv = run_dir / "grading_summary.csv"
        summary_csv = run_dir / "summary.csv"
        summary_xlsx = run_dir / "summary.xlsx"
        
        df = pd.DataFrame()
        
        # Try reading grading_summary.csv first
        if grading_csv.exists():
            try:
                df = pd.read_csv(grading_csv)
                if not df.empty:
                    return df
            except Exception as e:
                self.log(f"[WARNING] Failed to read grading_summary.csv: {e}\n")
                
        # Try reading summary.csv
        if summary_csv.exists():
            try:
                df = pd.read_csv(summary_csv)
            except Exception as e:
                self.log(f"[WARNING] Failed to read summary.csv: {e}\n")
                
        # If it is empty or missing suggested_status, check if summary.xlsx has it
        if df.empty or 'suggested_status' not in df.columns:
            if summary_xlsx.exists():
                try:
                    xlsx_df = pd.read_excel(summary_xlsx, sheet_name="Summary")
                    if not xlsx_df.empty:
                        # Prefer the xlsx data as it contains the final exported columns
                        df = xlsx_df
                except Exception as e:
                    self.log(f"[WARNING] Failed to read summary.xlsx: {e}\n")
                    
        return df

    def _load_summary_preview(self):
        df = self._load_summary_dataframe()
        
        # Clear existing summary columns and items
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.tree["columns"] = []

        if not df.empty:
            try:
                columns = list(df.columns)
                self.tree["columns"] = columns

                # Bind column headings and set default widths
                for col in columns:
                    self.tree.heading(col, text=col, anchor="w")
                    self.tree.column(col, width=120, minwidth=70, stretch=True, anchor="w")

                # Insert data rows
                for _, row in df.iterrows():
                    vals = ["" if pd.isna(val) else str(val) for val in row.values]
                    self.tree.insert("", "end", values=vals)
            except Exception as e:
                self.log(f"[WARNING] Failed to load summary preview table: {e}\n")

        # Populate metrics dashboard
        self._populate_dashboard(df)
        
        # Populate issues & review queue tab
        self._load_review_queue_data()

        # Update files tab status
        self._load_results_files_status()

    def _populate_dashboard(self, df: pd.DataFrame):
        # Clear existing dashboard widgets
        for widget in self.dash_frame.winfo_children():
            widget.destroy()

        if df.empty:
            lbl_msg = ttk.Label(
                self.dash_frame, 
                text="Export results not generated yet. Run Export Results or Full Pipeline.", 
                font=("Segoe UI", 11, "italic"),
                foreground="gray"
            )
            lbl_msg.pack(pady=20, anchor="center")
            return

        # If df is grading_summary, let's load summary.csv for the status distribution / details
        run_dir_str = self.run_dir_var.get().strip()
        run_dir = REPO_ROOT / run_dir_str
        summary_csv = run_dir / "summary.csv"
        
        df_summary = pd.DataFrame()
        if summary_csv.exists():
            try:
                df_summary = pd.read_csv(summary_csv)
            except Exception:
                pass

        total_subs = len(df)
        ok_restores = (df['manifest_status'] == 'OK').sum() if 'manifest_status' in df.columns else total_subs
        err_restores = total_subs - ok_restores

        metrics_frame = ttk.Frame(self.dash_frame)
        metrics_frame.pack(fill="x", expand=True, pady=5)
        metrics_frame.columnconfigure((0, 1, 2), weight=1)

        # Card 1: Submissions Status
        c_sub = ttk.LabelFrame(metrics_frame, text=" Submissions & Import ", padding=10)
        c_sub.grid(row=0, column=0, sticky="nsew", padx=5)
        ttk.Label(c_sub, text=f"Total Submissions: {total_subs}", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=2)
        ttk.Label(c_sub, text=f"Restore OK: {ok_restores}", foreground="green").pack(anchor="w")
        ttk.Label(c_sub, text=f"Restore Errors: {err_restores}", foreground="red" if err_restores > 0 else "gray").pack(anchor="w")

        # Card 2: Combined Issue Counts
        c_counts = ttk.LabelFrame(metrics_frame, text=" Global Grading Metrics ", padding=10)
        c_counts.grid(row=0, column=1, sticky="nsew", padx=5)
        
        has_final_metrics = ('hard_error_count' in df.columns) and ('manual_review_count' in df.columns)
        
        if 'auto_score' in df.columns:
            avg_auto = df['auto_score'].mean()
            avg_final = df['final_score'].mean()
            total_rev = df['review_required_count'].sum()
            total_err = df['hard_error_count'].sum()
            
            ttk.Label(c_counts, text=f"Avg Auto Score: {avg_auto:.2f}", font=("Segoe UI", 10, "bold")).pack(anchor="w")
            ttk.Label(c_counts, text=f"Avg Final Score: {avg_final:.2f}", font=("Segoe UI", 10, "bold"), foreground="green").pack(anchor="w")
            ttk.Label(c_counts, text=f"Total Review Required: {total_rev}", foreground="orange" if total_rev > 0 else "gray").pack(anchor="w")
            ttk.Label(c_counts, text=f"Total Hard Errors: {total_err}", foreground="red" if total_err > 0 else "gray").pack(anchor="w")
        elif has_final_metrics:
            he_total = df['hard_error_count'].sum()
            mr_total = df['manual_review_count'].sum()
            warn_total = df['warning_count'].sum() if 'warning_count' in df.columns else 0
            
            ttk.Label(c_counts, text=f"Hard Errors Total: {he_total}", font=("Segoe UI", 10, "bold"), foreground="red" if he_total > 0 else "black").pack(anchor="w")
            ttk.Label(c_counts, text=f"Manual Reviews Required: {mr_total}", font=("Segoe UI", 10, "bold"), foreground="orange" if mr_total > 0 else "black").pack(anchor="w")
            ttk.Label(c_counts, text=f"Warnings Total: {warn_total}", foreground="blue" if warn_total > 0 else "gray").pack(anchor="w")
        else:
            lbl_msg = ttk.Label(c_counts, text="Run Export Results first", font=("Segoe UI", 9, "italic"), foreground="gray")
            lbl_msg.pack(anchor="w", pady=10)

        # Card 3: Suggested Status Distribution
        c_status = ttk.LabelFrame(metrics_frame, text=" Status Recommendations ", padding=10)
        c_status.grid(row=0, column=2, sticky="nsew", padx=5)
        
        status_df = df_summary if not df_summary.empty else df
        if 'suggested_status' in status_df.columns:
            counts = status_df['suggested_status'].value_counts()
            for status, count in counts.items():
                lbl_color = "black"
                if "FAIL" in status:
                    lbl_color = "red"
                elif "REVIEW" in status:
                    lbl_color = "orange"
                elif "WARNING" in status:
                    lbl_color = "#005999"
                elif status == "PASS":
                    lbl_color = "green"
                ttk.Label(c_status, text=f"{status}: {count}", foreground=lbl_color, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        else:
            lbl_msg = ttk.Label(c_status, text="Run Export Results first", font=("Segoe UI", 9, "italic"), foreground="gray")
            lbl_msg.pack(anchor="w", pady=10)

        # Detailed breakdown Frame
        breakdown_frame = ttk.LabelFrame(self.dash_frame, text=" Detailed Mappings & Accuracy ", padding=10)
        breakdown_frame.pack(fill="x", expand=True, pady=15)
        breakdown_frame.columnconfigure((0, 1, 2), weight=1)

        # Views Breakdown
        v_frame = ttk.Frame(breakdown_frame, padding=5)
        v_frame.grid(row=0, column=0, sticky="n")
        ttk.Label(v_frame, text="Views Testing Stats", font=("Segoe UI", 10, "bold", "underline")).pack(anchor="w", pady=(0, 5))
        
        detail_df = df_summary if not df_summary.empty else df
        if 'view_pass_count' in detail_df.columns:
            v_pass = detail_df['view_pass_count'].sum()
            v_miss = detail_df['view_missing_count'].sum() if 'view_missing_count' in detail_df.columns else 0
            v_err = detail_df['view_execution_error_count'].sum() if 'view_execution_error_count' in detail_df.columns else 0
            v_mismatch = detail_df['view_value_mismatch_count'].sum() if 'view_value_mismatch_count' in detail_df.columns else 0
            ttk.Label(v_frame, text=f"Passing Views: {v_pass}", foreground="green").pack(anchor="w")
            ttk.Label(v_frame, text=f"Missing Views: {v_miss}", foreground="red" if v_miss > 0 else "gray").pack(anchor="w")
            ttk.Label(v_frame, text=f"Execution Errors: {v_err}", foreground="red" if v_err > 0 else "gray").pack(anchor="w")
            ttk.Label(v_frame, text=f"Value Mismatches: {v_mismatch}", foreground="red" if v_mismatch > 0 else "gray").pack(anchor="w")
        else:
            ttk.Label(v_frame, text="Test Views not run yet", font=("Segoe UI", 9, "italic"), foreground="gray").pack(anchor="w")

        # PK Breakdown
        pk_frame = ttk.Frame(breakdown_frame, padding=5)
        pk_frame.grid(row=0, column=1, sticky="n")
        ttk.Label(pk_frame, text="PK Adequacy Stats", font=("Segoe UI", 10, "bold", "underline")).pack(anchor="w", pady=(0, 5))
        
        pk_cols = ['pk_exact_match_count', 'pk_alias_equivalent_count', 'pk_surrogate_accepted_count', 'pk_natural_accepted_count', 'pk_alternative_accepted_count', 'pk_review_required_count', 'pk_missing_count', 'pk_invalid_count']
        if any(c in detail_df.columns for c in pk_cols):
            pk_acc = 0
            for col in ['pk_exact_match_count', 'pk_alias_equivalent_count', 'pk_surrogate_accepted_count', 'pk_natural_accepted_count', 'pk_alternative_accepted_count']:
                if col in detail_df.columns:
                    pk_acc += detail_df[col].sum()
            pk_rev = detail_df['pk_review_required_count'].sum() if 'pk_review_required_count' in detail_df.columns else 0
            pk_miss = 0
            for col in ['pk_missing_count', 'pk_invalid_count']:
                if col in detail_df.columns:
                    pk_miss += detail_df[col].sum()
            ttk.Label(pk_frame, text=f"Accepted PKs: {pk_acc}", foreground="green").pack(anchor="w")
            ttk.Label(pk_frame, text=f"Review Required: {pk_rev}", foreground="orange" if pk_rev > 0 else "gray").pack(anchor="w")
            ttk.Label(pk_frame, text=f"Missing/Invalid PKs: {pk_miss}", foreground="red" if pk_miss > 0 else "gray").pack(anchor="w")
        else:
            ttk.Label(pk_frame, text="Structure compare not run yet", font=("Segoe UI", 9, "italic"), foreground="gray").pack(anchor="w")

        # FK Breakdown
        fk_frame = ttk.Frame(breakdown_frame, padding=5)
        fk_frame.grid(row=0, column=2, sticky="n")
        ttk.Label(fk_frame, text="FK Relationships Stats", font=("Segoe UI", 10, "bold", "underline")).pack(anchor="w", pady=(0, 5))
        
        fk_cols = ['fk_exact_match_count', 'fk_relationship_match_count', 'fk_alias_equivalent_count', 'fk_surrogate_accepted_count', 'fk_natural_accepted_count', 'fk_review_required_count', 'fk_missing_count', 'fk_wrong_target_count']
        if any(c in detail_df.columns for c in fk_cols):
            fk_acc = 0
            for col in ['fk_exact_match_count', 'fk_relationship_match_count', 'fk_alias_equivalent_count', 'fk_surrogate_accepted_count', 'fk_natural_accepted_count']:
                if col in detail_df.columns:
                    fk_acc += detail_df[col].sum()
            fk_rev = detail_df['fk_review_required_count'].sum() if 'fk_review_required_count' in detail_df.columns else 0
            fk_miss = 0
            for col in ['fk_missing_count', 'fk_wrong_target_count']:
                if col in detail_df.columns:
                    fk_miss += detail_df[col].sum()
            ttk.Label(fk_frame, text=f"Accepted FKs: {fk_acc}", foreground="green").pack(anchor="w")
            ttk.Label(fk_frame, text=f"Review Required: {fk_rev}", foreground="orange" if fk_rev > 0 else "gray").pack(anchor="w")
            ttk.Label(fk_frame, text=f"Missing/Wrong FKs: {fk_miss}", foreground="red" if fk_miss > 0 else "gray").pack(anchor="w")
        else:
            ttk.Label(fk_frame, text="Structure compare not run yet", font=("Segoe UI", 9, "italic"), foreground="gray").pack(anchor="w")

    def _read_combined_issues(self, run_dir: Path) -> List[Dict[str, Any]]:
        items = []
        rq_file = run_dir / "review_queue.csv"
        he_file = run_dir / "hard_errors.csv"
        
        if rq_file.exists():
            try:
                with open(rq_file, "r", encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        row["category"] = "Review Required"
                        items.append(row)
            except Exception as e:
                self.log(f"[WARNING] Failed to read review queue CSV: {e}\n")
                
        if he_file.exists():
            try:
                with open(he_file, "r", encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        row["category"] = "Hard Error"
                        items.append(row)
            except Exception as e:
                self.log(f"[WARNING] Failed to read hard errors CSV: {e}\n")
                
        return items

    def _load_review_queue_data(self):
        # Clear existing items
        for item in self.rq_tree.get_children():
            self.rq_tree.delete(item)
            
        run_dir_str = self.run_dir_var.get().strip()
        if not run_dir_str:
            return
            
        run_dir = REPO_ROOT / run_dir_str
        items = self._read_combined_issues(run_dir)
        if not items:
            return
            
        filter_val = self.rq_filter_var.get()
        sub_id_filter = self.rq_sub_id_var.get().strip().lower()
        report_filter = self.rq_report_var.get().strip().lower()
        component_filter = self.rq_component_var.get().strip().lower()
        status_filter = self.rq_status_var.get().strip().lower()
        severity_filter = self.rq_severity_var.get().strip().lower()
        
        filtered = []
        for row in items:
            cat = row.get("category", "")
            comp = row.get("component", "")
            status = row.get("status", "")
            src_rep = row.get("source_report", "")
            sub_id = row.get("submission_id", "")
            severity = row.get("severity", "")
            
            # Category filters
            if filter_val == "Hard Errors Only" and cat != "Hard Error":
                continue
            elif filter_val == "Review Required Only" and cat != "Review Required":
                continue
            elif filter_val == "View Issues Only" and not (comp.lower() == "view" or "view" in src_rep.lower()):
                continue
            elif filter_val == "PK/FK Issues Only" and not (comp.lower() in ("primary_key", "foreign_key") or any(x in src_rep.lower() for x in ("key_adequacy", "fk_relationship"))):
                continue
            elif filter_val == "Mapping Issues Only" and not ("mapping" in src_rep.lower() or "ambiguous" in status.lower() or "unmapped" in status.lower() or status == "EXTRA_REVIEW"):
                continue
                
            # Entry filters (case insensitive substring matches)
            if sub_id_filter and sub_id_filter not in sub_id.lower():
                continue
            if report_filter and report_filter not in src_rep.lower():
                continue
            if component_filter and component_filter not in comp.lower():
                continue
            if status_filter and status_filter not in status.lower():
                continue
            if severity_filter and severity_filter not in severity.lower():
                continue
                
            filtered.append(row)
            
        for row in filtered:
            vals = [
                row.get("submission_id", ""),
                row.get("category", ""),
                row.get("source_report", ""),
                row.get("component", ""),
                row.get("status", ""),
                row.get("severity", ""),
                row.get("message", ""),
                row.get("suggested_action", ""),
                row.get("evidence", "")
            ]
            self.rq_tree.insert("", "end", values=vals)

    def _load_results_files_status(self):
        run_dir_str = self.run_dir_var.get().strip()
        if not run_dir_str:
            for lbl_status, btn_open, _ in self.file_widgets:
                lbl_status.config(text="[Not generated]", foreground="gray")
                btn_open.config(state="disabled")
            return

        run_dir = REPO_ROOT / run_dir_str

        # Determine currently selected submission ID from the grading table (column 0)
        selected_sub_id = ""
        selected = self.tree.selection()
        if selected:
            row_values = self.tree.item(selected[0], "values")
            if row_values:
                selected_sub_id = str(row_values[0])

        for lbl_status, btn_open, subpath in self.file_widgets:
            # Submission-dependent view_sql paths
            if isinstance(subpath, str) and subpath.startswith("view_sql:"):
                rel = subpath[len("view_sql:"):]
                if not selected_sub_id:
                    lbl_status.config(text="[Select student first]", foreground="gray")
                    btn_open.config(state="disabled")
                    continue
                path = run_dir / "submissions" / selected_sub_id / rel
                if path.exists():
                    lbl_status.config(text="✔ Available", foreground="green")
                    btn_open.config(state="normal", command=lambda p=path: self._safe_open_path(p))
                else:
                    lbl_status.config(text="[Not generated]", foreground="gray")
                    btn_open.config(state="disabled")
                continue

            # Regular run-dir-relative paths
            if not subpath:
                path = run_dir
            else:
                path = run_dir / subpath

            if path.exists():
                lbl_status.config(text="✔ Available", foreground="green")
                btn_open.config(state="normal", command=lambda p=path: self._safe_open_path(p))
            else:
                lbl_status.config(text="[Not generated]", foreground="gray")
                btn_open.config(state="disabled")

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
        run_dir = REPO_ROOT / run_dir_str
        grading_path = run_dir / "grading_summary.csv"
        summary_path = run_dir / "summary.csv"
        
        path_to_open = grading_path if grading_path.exists() else summary_path
        self._safe_open_path(path_to_open)

    def _open_mapping_reports(self):
        run_dir_str = self.run_dir_var.get().strip()
        if not run_dir_str:
            messagebox.showerror("Error", "Run directory path is empty.")
            return

        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Please select a student submission from the preview table.")
            return

        row_values = self.tree.item(selected[0], "values")
        if not row_values:
            return

        student_id = row_values[0]  # submission_id is column 0
        reports_dir = REPO_ROOT / run_dir_str / "submissions" / student_id / "reports"
        self._safe_open_path(reports_dir)

    def _update_rubric_total(self, *args):
        total = 0.0
        for var in [self.pt_tables, self.pt_columns, self.pt_pks, self.pt_fks, self.pt_row_counts, self.pt_manual]:
            try:
                total += float(var.get() or 0.0)
            except ValueError:
                pass
        for var in self.pt_views.values():
            try:
                total += float(var.get() or 0.0)
            except ValueError:
                pass
                
        self.lbl_rubric_total.config(text=f"Total Rubric Points: {total:.2f}")
        if abs(total - 10.0) > 0.001:
            self.lbl_rubric_total.config(foreground="orange")
            self.lbl_rubric_warning.config(text="⚠ Warning: Total points should sum to exactly 10.0.")
        else:
            self.lbl_rubric_total.config(foreground="green")
            self.lbl_rubric_warning.config(text="")

    def _save_rubric(self):
        run_dir_str = self.run_dir_var.get().strip()
        if not run_dir_str:
            messagebox.showerror("Error", "Run directory path is empty.")
            return
            
        try:
            pts_tables = float(self.pt_tables.get() or 0.0)
            pts_columns = float(self.pt_columns.get() or 0.0)
            pts_pks = float(self.pt_pks.get() or 0.0)
            pts_fks = float(self.pt_fks.get() or 0.0)
            pts_row_counts = float(self.pt_row_counts.get() or 0.0)
            pts_manual = float(self.pt_manual.get() or 0.0)
            
            view_pts = {}
            for v_name, var in self.pt_views.items():
                view_pts[v_name] = float(var.get() or 0.0)
        except ValueError:
            messagebox.showerror("Error", "All point values must be valid numbers.")
            return
            
        total = pts_tables + pts_columns + pts_pks + pts_fks + pts_row_counts + pts_manual + sum(view_pts.values())
        if abs(total - 10.0) > 0.001:
            messagebox.showwarning("Warning", f"Total rubric points ({total:.2f}) does not equal 10.0.")
            
        run_dir = REPO_ROOT / run_dir_str
        run_dir.mkdir(parents=True, exist_ok=True)
        rubric_csv = run_dir / "grading_rubric.csv"
        
        try:
            with open(rubric_csv, "w", newline="", encoding="utf-8") as f:
                headers = ["section", "component", "scope", "object_name", "total_points", "scoring_mode", "include_statuses", "partial_policy", "notes"]
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()
                
                # Tables
                writer.writerow({
                    "section": "A", "component": "tables", "scope": "all", "object_name": "",
                    "total_points": pts_tables, "scoring_mode": "proportional",
                    "include_statuses": "TABLE_MATCHED_EXACT|TABLE_MATCHED_ALIAS|TABLE_MATCHED_ABBREVIATION|TABLE_MATCHED_FUZZY_HIGH|TABLE_MATCHED_WEAK_ALIAS",
                    "partial_policy": "review_pending", "notes": "Proportional tables mapping points"
                })
                # Columns
                writer.writerow({
                    "section": "B", "component": "columns", "scope": "all", "object_name": "",
                    "total_points": pts_columns, "scoring_mode": "proportional",
                    "include_statuses": "COLUMN_MATCHED_EXACT|COLUMN_MATCHED_ALIAS|COLUMN_MATCHED_ABBREVIATION|COLUMN_MATCHED_WEAK_ALIAS",
                    "partial_policy": "review_pending", "notes": "Proportional columns mapping points"
                })
                # Primary Keys
                writer.writerow({
                    "section": "C", "component": "primary_keys", "scope": "all", "object_name": "",
                    "total_points": pts_pks, "scoring_mode": "proportional",
                    "include_statuses": "PK_MATCH_EXACT|PK_MATCH_ALIAS_EQUIVALENT|PK_SURROGATE_ACCEPTED|PK_NATURAL_ACCEPTED",
                    "partial_policy": "review_pending", "notes": "Proportional primary keys points"
                })
                # Foreign Keys
                writer.writerow({
                    "section": "D", "component": "foreign_keys", "scope": "all", "object_name": "",
                    "total_points": pts_fks, "scoring_mode": "proportional",
                    "include_statuses": "FK_MATCH_EXACT|FK_ALIAS_EQUIVALENT|FK_SURROGATE_ACCEPTED|FK_NATURAL_ACCEPTED",
                    "partial_policy": "review_pending", "notes": "Proportional foreign keys points"
                })
                # Row Counts
                writer.writerow({
                    "section": "E", "component": "row_counts", "scope": "all", "object_name": "",
                    "total_points": pts_row_counts, "scoring_mode": "proportional",
                    "include_statuses": "PASS",
                    "partial_policy": "review_pending", "notes": "Proportional row count check points"
                })
                # Views
                for v_name, pts in view_pts.items():
                    writer.writerow({
                        "section": "F", "component": "views", "scope": v_name, "object_name": "",
                        "total_points": pts, "scoring_mode": "weighted_subchecks",
                        "include_statuses": "VIEW_PASS",
                        "partial_policy": "partial_view", "notes": f"Weighted view check points for {v_name}"
                    })
                # Manual
                writer.writerow({
                    "section": "M", "component": "manual", "scope": "all", "object_name": "",
                    "total_points": pts_manual, "scoring_mode": "manual_only",
                    "include_statuses": "",
                    "partial_policy": "", "notes": "Optional manual grading section"
                })
                
            self.log(f"[INFO] Rubric saved to: {os.path.relpath(rubric_csv, REPO_ROOT)}\n")
            self._load_results_files_status()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save rubric: {e}")

    def _load_rubric_file(self):
        run_dir_str = self.run_dir_var.get().strip()
        if not run_dir_str:
            messagebox.showerror("Error", "Run directory path is empty.")
            return
        run_dir = REPO_ROOT / run_dir_str
        rubric_csv = run_dir / "grading_rubric.csv"
        
        if not rubric_csv.exists():
            messagebox.showinfo("Not Found", f"Rubric file grading_rubric.csv not found in run directory. Creating defaults.")
            self.pt_tables.set("1.0")
            self.pt_columns.set("2.0")
            self.pt_pks.set("1.0")
            self.pt_fks.set("1.0")
            self.pt_row_counts.set("1.0")
            self.pt_manual.set("0.0")
            for var in self.pt_views.values():
                var.set("1.0")
            return
            
        try:
            with open(rubric_csv, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                view_pts_found = {}
                for row in reader:
                    comp = row["component"]
                    scope = row["scope"]
                    pts = row["total_points"]
                    
                    if comp == "tables":
                        self.pt_tables.set(pts)
                    elif comp == "columns":
                        self.pt_columns.set(pts)
                    elif comp == "primary_keys":
                        self.pt_pks.set(pts)
                    elif comp == "foreign_keys":
                        self.pt_fks.set(pts)
                    elif comp == "row_counts":
                        self.pt_row_counts.set(pts)
                    elif comp == "manual":
                        self.pt_manual.set(pts)
                    elif comp == "views":
                        view_pts_found[scope] = pts
                        
                for v_name, var in self.pt_views.items():
                    if v_name in view_pts_found:
                        var.set(view_pts_found[v_name])
                    else:
                        var.set("1.0")
            self.log(f"[INFO] Rubric loaded from: {os.path.relpath(rubric_csv, REPO_ROOT)}\n")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load rubric: {e}")

    def _open_grading_summary_xlsx(self):
        run_dir_str = self.run_dir_var.get().strip()
        if not run_dir_str:
            messagebox.showerror("Error", "Run directory path is empty.")
            return
        path = REPO_ROOT / run_dir_str / "grading_summary.xlsx"
        self._safe_open_path(path)

# --- Launcher Main Function ---

def main():
    root = tk.Tk()
    app = App(root)
    root.mainloop()

if __name__ == "__main__":
    main()
