import os
import re
import sys
import shutil
import queue
import threading
import zipfile
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import customtkinter as ctk
except ImportError:
    print("Falta instalar customtkinter. Ejecuta: pip install customtkinter")
    raise

from tkinter import filedialog, messagebox


# -----------------------------
# Configuración visual
# -----------------------------
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

APP_TITLE = "ZIP Extractor & Renamer"
APP_SIZE = "1180x760"

BG = "#121826"
PANEL = "#1A2332"
PANEL_2 = "#202B3D"
ACCENT = "#4F8CFF"
SUCCESS = "#2ECC71"
WARNING = "#F5B041"
ERROR = "#E74C3C"
TEXT = "#EAF2FF"
MUTED = "#9FB0C8"

SEPARATOR_REGEX = re.compile(r"^-{20,}\s*$", re.MULTILINE)
ID_REGEX = re.compile(r"\b(\d{8})\b")
PREF_REGEX = re.compile(r"\b(P-\d+)\b")


@dataclass
class ZipJob:
    zip_path: str
    id_value: str
    prefix: str
    final_name: str
    output_path: str
    status: str = "Pendiente"


class ZipExtractorRenamerApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.geometry(APP_SIZE)
        self.minsize(1080, 700)
        self.configure(fg_color=BG)

        self.selected_zips = []
        self.selected_txt = ""
        self.selected_destination = ""
        self.mapping = {}
        self.jobs = []
        self.cancel_event = threading.Event()
        self.ui_queue = queue.Queue()
        self.worker_thread = None
        self.processing = False

        self._build_ui()
        self.after(100, self._poll_ui_queue)

    # -----------------------------
    # UI
    # -----------------------------
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=20)
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 10))
        header.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(
            header,
            text="Extractor y Renombrador de ZIPs",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=TEXT,
        )
        title.grid(row=0, column=0, sticky="w", padx=18, pady=(16, 4))

        subtitle = ctk.CTkLabel(
            header,
            text="Selecciona ZIPs, TXT y carpeta de destino. El programa extrae, renombra y muestra progreso en tiempo real.",
            font=ctk.CTkFont(size=13),
            text_color=MUTED,
        )
        subtitle.grid(row=1, column=0, sticky="w", padx=18, pady=(0, 16))

        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=1, column=0, sticky="nsew", padx=18, pady=10)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)

        # Panel superior de selección
        select_panel = ctk.CTkFrame(main, fg_color=PANEL, corner_radius=20)
        select_panel.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        for i in range(4):
            select_panel.grid_columnconfigure(i, weight=1)

        self.btn_zips = self._rounded_button(select_panel, "Seleccionar ZIPs", self.select_zips)
        self.btn_zips.grid(row=0, column=0, padx=12, pady=14, sticky="ew")

        self.btn_txt = self._rounded_button(select_panel, "Seleccionar TXT", self.select_txt)
        self.btn_txt.grid(row=0, column=1, padx=12, pady=14, sticky="ew")

        self.btn_dest = self._rounded_button(select_panel, "Seleccionar destino", self.select_destination)
        self.btn_dest.grid(row=0, column=2, padx=12, pady=14, sticky="ew")

        self.btn_start = self._rounded_button(select_panel, "Iniciar proceso", self.start_processing, accent=True)
        self.btn_start.grid(row=0, column=3, padx=12, pady=14, sticky="ew")

        # Panel central
        center = ctk.CTkFrame(main, fg_color="transparent")
        center.grid(row=1, column=0, sticky="nsew")
        center.grid_columnconfigure(0, weight=3)
        center.grid_columnconfigure(1, weight=2)
        center.grid_rowconfigure(0, weight=1)

        # Tabla / lista de trabajos
        table_panel = ctk.CTkFrame(center, fg_color=PANEL, corner_radius=20)
        table_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        table_panel.grid_rowconfigure(1, weight=1)
        table_panel.grid_columnconfigure(0, weight=1)

        table_header = ctk.CTkFrame(table_panel, fg_color=PANEL_2, corner_radius=16)
        table_header.grid(row=0, column=0, sticky="ew", padx=12, pady=12)
        table_header.grid_columnconfigure((0, 1, 2, 3), weight=1)

        headers = ["ZIP", "ID", "Nombre final", "Estado"]
        for idx, h in enumerate(headers):
            lbl = ctk.CTkLabel(table_header, text=h, font=ctk.CTkFont(size=13, weight="bold"), text_color=TEXT)
            lbl.grid(row=0, column=idx, padx=10, pady=10, sticky="w")

        self.table_scroll = ctk.CTkScrollableFrame(table_panel, fg_color="transparent", corner_radius=12)
        self.table_scroll.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.table_scroll.grid_columnconfigure((0, 1, 2, 3), weight=1)

        # Panel derecho: logs y resumen
        side = ctk.CTkFrame(center, fg_color=PANEL, corner_radius=20)
        side.grid(row=0, column=1, sticky="nsew")
        side.grid_rowconfigure(2, weight=1)
        side.grid_columnconfigure(0, weight=1)

        self.summary_label = ctk.CTkLabel(
            side,
            text="Archivos: 0 | Procesados: 0 | Errores: 0",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=TEXT,
        )
        self.summary_label.grid(row=0, column=0, padx=14, pady=(14, 8), sticky="w")

        self.global_progress = ctk.CTkProgressBar(side, height=18, corner_radius=8)
        self.global_progress.grid(row=1, column=0, padx=14, pady=(0, 12), sticky="ew")
        self.global_progress.set(0)

        logs_frame = ctk.CTkFrame(side, fg_color=PANEL_2, corner_radius=16)
        logs_frame.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 14))
        logs_frame.grid_rowconfigure(1, weight=1)
        logs_frame.grid_columnconfigure(0, weight=1)

        logs_title = ctk.CTkLabel(logs_frame, text="Registro en tiempo real", font=ctk.CTkFont(size=13, weight="bold"), text_color=TEXT)
        logs_title.grid(row=0, column=0, sticky="w", padx=12, pady=(12, 8))

        self.log_text = ctk.CTkTextbox(logs_frame, wrap="word", fg_color="#0E1520", text_color=TEXT, corner_radius=12)
        self.log_text.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.log_text.configure(state="disabled")

        # Barra inferior
        footer = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=20)
        footer.grid(row=2, column=0, sticky="ew", padx=18, pady=(10, 18))
        footer.grid_columnconfigure(0, weight=1)
        footer.grid_columnconfigure(1, weight=1)
        footer.grid_columnconfigure(2, weight=1)

        self.status_label = ctk.CTkLabel(footer, text="Listo", font=ctk.CTkFont(size=13), text_color=MUTED)
        self.status_label.grid(row=0, column=0, padx=16, pady=16, sticky="w")

        self.workers_label = ctk.CTkLabel(footer, text="Hilos: 4", font=ctk.CTkFont(size=13), text_color=MUTED)
        self.workers_label.grid(row=0, column=1, padx=16, pady=16)

        self.cancel_btn = self._rounded_button(footer, "Cancelar", self.cancel_processing, width=180)
        self.cancel_btn.grid(row=0, column=2, padx=16, pady=16, sticky="e")
        self.cancel_btn.configure(state="disabled")

    def _rounded_button(self, parent, text, command, accent=False, width=170):
        fg = ACCENT if accent else "#243247"
        hover = "#6A9DFF" if accent else "#31435E"
        return ctk.CTkButton(
            parent,
            text=text,
            command=command,
            width=width,
            height=42,
            corner_radius=22,
            fg_color=fg,
            hover_color=hover,
            text_color="white",
            font=ctk.CTkFont(size=13, weight="bold"),
        )

    # -----------------------------
    # Selección de archivos
    # -----------------------------
    def select_zips(self):
        if self.processing:
            messagebox.showwarning("En proceso", "No puedes cambiar la selección mientras está corriendo.")
            return

        files = filedialog.askopenfilenames(
            title="Selecciona archivos ZIP",
            filetypes=[("Archivos ZIP", "*.zip")],
        )
        if files:
            self.selected_zips = list(files)
            self._rebuild_jobs_preview()
            self._log(f"[OK] ZIPs seleccionados: {len(self.selected_zips)}")
            self._refresh_summary()

    def select_txt(self):
        if self.processing:
            messagebox.showwarning("En proceso", "No puedes cambiar la selección mientras está corriendo.")
            return

        file = filedialog.askopenfilename(
            title="Selecciona archivo TXT",
            filetypes=[("Archivos TXT", "*.txt"), ("Todos", "*.*")],
        )
        if file:
            self.selected_txt = file
            self._log(f"[OK] TXT seleccionado: {os.path.basename(file)}")
            self._parse_txt_file()
            self._rebuild_jobs_preview()
            self._refresh_summary()

    def select_destination(self):
        if self.processing:
            messagebox.showwarning("En proceso", "No puedes cambiar la selección mientras está corriendo.")
            return

        folder = filedialog.askdirectory(title="Selecciona carpeta destino")
        if folder:
            self.selected_destination = folder
            self._log(f"[OK] Destino seleccionado: {folder}")
            self._rebuild_jobs_preview()
            self._refresh_summary()

    # -----------------------------
    # TXT parsing
    # -----------------------------
    def _parse_txt_file(self):
        self.mapping = {}
        if not self.selected_txt:
            return

        try:
            with open(self.selected_txt, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception as e:
            self._log(f"[ERROR] No se pudo leer el TXT: {e}")
            messagebox.showerror("Error", f"No se pudo leer el TXT:\n{e}")
            return

        blocks = [b.strip() for b in SEPARATOR_REGEX.split(content) if b.strip()]
        for block in blocks:
            id_match = ID_REGEX.search(block)
            pref_match = PREF_REGEX.search(block)
            if id_match and pref_match:
                id_value = id_match.group(1)
                prefix = pref_match.group(1)
                self.mapping[id_value] = prefix

        self._log(f"[OK] Registros leídos del TXT: {len(self.mapping)}")
        if not self.mapping:
            self._log("[AVISO] No se encontraron pares ID / P-XXXXXX en el TXT.")

    # -----------------------------
    # Preparar trabajos
    # -----------------------------
    def _extract_id_from_zip_name(self, zip_path: str):
        base = os.path.basename(zip_path)

        # Limpieza de espacios y caracteres invisibles
        base = base.strip()

        # Buscar específicamente el patrón mp_e57_########_
        match = re.search(r"mp_e57_(\d{8})", base, re.IGNORECASE)
        if match:
            return match.group(1)

        # Fallback: cualquier bloque de 8 dígitos
        match = re.search(r"(\d{8})", base)
        if match:
            return match.group(1)

        return None

    def _rebuild_jobs_preview(self):
        self._clear_table()
        self.jobs = []

        if not self.selected_zips:
            return

        for zip_path in self.selected_zips:
            base = os.path.basename(zip_path)
            id_value = self._extract_id_from_zip_name(zip_path)
            prefix = self.mapping.get(id_value, "SIN_PREFIJO") if id_value else "SIN_ID"
            final_name = f"{prefix}_{id_value}" if id_value and prefix != "SIN_PREFIJO" else "SIN_CORRESPONDENCIA"
            output_path = os.path.join(self.selected_destination, final_name) if self.selected_destination else ""
            job = ZipJob(zip_path=zip_path, id_value=id_value or "", prefix=prefix, final_name=final_name, output_path=output_path)
            self.jobs.append(job)

        for job in self.jobs:
            self._add_table_row(job)

    def _clear_table(self):
        for widget in self.table_scroll.winfo_children():
            widget.destroy()

    def _add_table_row(self, job: ZipJob):
        row = ctk.CTkFrame(self.table_scroll, fg_color="#17202D", corner_radius=14)
        row.pack(fill="x", pady=6, padx=2)
        row.grid_columnconfigure((0, 1, 2, 3), weight=1)

        cols = [
            os.path.basename(job.zip_path),
            job.id_value or "No encontrado",
            job.final_name,
            job.status,
        ]

        for idx, text in enumerate(cols):
            label = ctk.CTkLabel(
                row,
                text=text,
                font=ctk.CTkFont(size=12),
                text_color=TEXT if idx != 3 else MUTED,
                anchor="w",
                justify="left",
            )
            label.grid(row=0, column=idx, padx=10, pady=12, sticky="w")

        job._ui_row = row  # type: ignore[attr-defined]
        job._ui_labels = row.winfo_children()  # type: ignore[attr-defined]

    def _refresh_row(self, job: ZipJob):
        try:
            widgets = job._ui_row.winfo_children()  # type: ignore[attr-defined]
            if len(widgets) >= 4:
                widgets[3].configure(text=job.status)
        except Exception:
            pass

    # -----------------------------
    # Procesamiento
    # -----------------------------
    def start_processing(self):
        if self.processing:
            return

        if not self.selected_zips:
            messagebox.showwarning("Faltan archivos", "Selecciona uno o más ZIPs.")
            return
        if not self.selected_txt:
            messagebox.showwarning("Falta TXT", "Selecciona el archivo TXT de referencia.")
            return
        if not self.selected_destination:
            messagebox.showwarning("Falta destino", "Selecciona la carpeta destino.")
            return

        if not self.mapping:
            self._parse_txt_file()
            if not self.mapping:
                messagebox.showerror("TXT vacío o inválido", "No se encontraron coincidencias ID / P-XXXXXX en el TXT.")
                return

        # Actualizar trabajos según el TXT cargado
        self._rebuild_jobs_preview()

        missing = [job.zip_path for job in self.jobs if not job.id_value]
        if missing:
            messagebox.showwarning(
                "ZIP sin ID",
                "Algunos ZIPs no contienen un ID de 8 dígitos en el nombre. Esos archivos no se procesarán correctamente.",
            )

        self.processing = True
        self.cancel_event.clear()
        self.btn_start.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.status_label.configure(text="Procesando...")
        self._log("[INFO] Inicio de procesamiento.")

        max_workers = self._choose_workers(len(self.jobs))
        self.workers_label.configure(text=f"Hilos: {max_workers}")

        self.worker_thread = threading.Thread(target=self._process_all, args=(max_workers,), daemon=True)
        self.worker_thread.start()

    def _choose_workers(self, n_jobs: int):
        cpu = os.cpu_count() or 4
        # Equilibrio entre velocidad y uso de disco/CPU
        if n_jobs <= 2:
            return 2
        return max(2, min(6, cpu, n_jobs))

    def _process_all(self, max_workers: int):
        total = len(self.jobs)
        done = 0
        errors = 0

        self.ui_queue.put(("summary", total, done, errors))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_job = {executor.submit(self._process_single_job, job): job for job in self.jobs}

            for future in as_completed(future_to_job):
                if self.cancel_event.is_set():
                    break

                job = future_to_job[future]
                try:
                    ok, message = future.result()
                    if ok:
                        done += 1
                    else:
                        errors += 1
                    self.ui_queue.put(("job_result", job, ok, message))
                except Exception as e:
                    errors += 1
                    self.ui_queue.put(("job_result", job, False, f"Excepción: {e}"))

                processed = done + errors
                progress = processed / total if total else 0
                self.ui_queue.put(("progress", progress, total, done, errors))

        if self.cancel_event.is_set():
            self.ui_queue.put(("done", False, "Proceso cancelado por el usuario."))
        else:
            self.ui_queue.put(("done", True, "Proceso terminado."))

    def _process_single_job(self, job: ZipJob):
        if self.cancel_event.is_set():
            return False, "Cancelado"

        if not job.id_value:
            job.status = "Sin ID"
            return False, "No se encontró ID en el ZIP"

        prefix = self.mapping.get(job.id_value)
        if not prefix:
            job.status = "Sin coincidencia en TXT"
            return False, f"No hay prefijo para ID {job.id_value}"

        final_name = f"{prefix}_{job.id_value}"
        output_folder = os.path.join(self.selected_destination, final_name)
        job.final_name = final_name
        job.output_path = output_folder
        job.status = "Extrayendo..."

        try:
            os.makedirs(output_folder, exist_ok=True)

            with zipfile.ZipFile(job.zip_path, "r") as zf:
                bad = zf.testzip()
                if bad is not None:
                    job.status = "ZIP dañado"
                    return False, f"ZIP corrupto, primer archivo con problema: {bad}"
                zf.extractall(output_folder)

            # Registrar archivo ZIP original dentro de la carpeta destino para trazabilidad
            try:
                shutil.copy2(job.zip_path, os.path.join(output_folder, os.path.basename(job.zip_path)))
            except Exception:
                pass

            job.status = "Completado"
            return True, f"Extraído y renombrado a {final_name}"
        except zipfile.BadZipFile:
            job.status = "ZIP inválido"
            return False, "Archivo ZIP inválido"
        except PermissionError:
            job.status = "Sin permisos"
            return False, "Permiso denegado en destino"
        except Exception as e:
            job.status = "Error"
            return False, str(e)

    def cancel_processing(self):
        if not self.processing:
            return
        self.cancel_event.set()
        self._log("[AVISO] Cancelación solicitada...")
        self.status_label.configure(text="Cancelando...")
        self.cancel_btn.configure(state="disabled")

    # -----------------------------
    # Queue / UI updates
    # -----------------------------
    def _poll_ui_queue(self):
        try:
            while True:
                item = self.ui_queue.get_nowait()
                kind = item[0]

                if kind == "summary":
                    total, done, errors = item[1], item[2], item[3]
                    self._update_summary(total, done, errors)

                elif kind == "progress":
                    progress, total, done, errors = item[1], item[2], item[3], item[4]
                    self.global_progress.set(progress)
                    self._update_summary(total, done, errors)

                elif kind == "job_result":
                    job, ok, message = item[1], item[2], item[3]
                    job.status = "Completado" if ok else "Error"
                    self._refresh_row(job)
                    tag = "[OK]" if ok else "[ERROR]"
                    self._log(f"{tag} {os.path.basename(job.zip_path)} -> {message}")

                elif kind == "done":
                    ok, message = item[1], item[2]
                    self.processing = False
                    self.btn_start.configure(state="normal")
                    self.cancel_btn.configure(state="disabled")
                    self.status_label.configure(text="Listo" if ok else "Cancelado")
                    self._log(f"[INFO] {message}")

        except queue.Empty:
            pass

        self.after(120, self._poll_ui_queue)

    def _update_summary(self, total, done, errors):
        self.summary_label.configure(text=f"Archivos: {total} | Procesados: {done} | Errores: {errors}")

    def _refresh_summary(self):
        total = len(self.jobs)
        done = sum(1 for j in self.jobs if j.status == "Completado")
        errors = sum(1 for j in self.jobs if j.status not in ("Pendiente", "Extrayendo...", "Completado") and j.status != "")
        self._update_summary(total, done, errors)

    def _log(self, message: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # -----------------------------
    # Run
    # -----------------------------


def main():
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    app = ZipExtractorRenamerApp()
    app.mainloop()


if __name__ == "__main__":
    main()