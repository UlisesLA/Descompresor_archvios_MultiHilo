import os
import re
import sys
import queue
import hashlib
import threading
import zipfile
from dataclasses import dataclass

try:
    import customtkinter as ctk
except ImportError:
    print("Falta instalar customtkinter. Ejecuta: pip install customtkinter")
    raise

from tkinter import filedialog, messagebox


# -----------------------------
# Configuración visual – MODO CLARO
# -----------------------------
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

APP_TITLE = "ZIP Extractor & Renamer"
APP_SIZE  = "1220x780"

BG      = "#F0F4FA"
PANEL   = "#FFFFFF"
PANEL_2 = "#EDF1F8"
ACCENT  = "#2563EB"
SUCCESS = "#16A34A"
WARNING = "#D97706"
ERROR   = "#DC2626"
TEXT    = "#1E293B"
MUTED   = "#64748B"
LOG_BG  = "#F8FAFC"
ROW_BG  = "#F4F7FC"
BORDER  = "#DDE4EF"

# Enviar actualización de progreso cada 512 KB leídos
UPDATE_BYTES = 512 * 1024

SEPARATOR_REGEX = re.compile(r"^-{20,}\s*$", re.MULTILINE)
ID_REGEX        = re.compile(r"\b(\d{8})\b")
PREF_REGEX      = re.compile(r"\b(P-\d+)\b")


@dataclass
class ZipJob:
    zip_path:   str
    id_value:   str
    prefix:     str
    final_name: str
    output_path: str
    status: str = "Pendiente"


class ZipExtractorRenamerApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.geometry(APP_SIZE)
        self.minsize(1100, 700)
        self.configure(fg_color=BG)

        self.selected_zips        = []
        self.selected_txt         = ""
        self.selected_destination = ""
        self.mapping              = {}
        self.jobs                 = []
        self.cancel_event         = threading.Event()
        self.ui_queue             = queue.Queue()
        self.worker_thread        = None
        self.processing           = False

        self._build_ui()
        self.after(100, self._poll_ui_queue)

    # ──────────────────────────────────────────────
    # UI
    # ──────────────────────────────────────────────
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ── Header ──────────────────────────────────
        header = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=20,
                              border_width=1, border_color=BORDER)
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 10))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header, text="Extractor y Renombrador de ZIPs",
            font=ctk.CTkFont(size=24, weight="bold"), text_color=TEXT,
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(16, 4))

        ctk.CTkLabel(
            header,
            text=(
                "Selecciona ZIPs, TXT y carpeta destino. "
                "Extrae, renombra el E57, valida integridad SHA-256 y muestra progreso en tiempo real."
            ),
            font=ctk.CTkFont(size=13), text_color=MUTED,
        ).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 16))

        # ── Área principal ───────────────────────────
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=1, column=0, sticky="nsew", padx=18, pady=10)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)

        # Panel de selección
        select_panel = ctk.CTkFrame(main, fg_color=PANEL, corner_radius=20,
                                    border_width=1, border_color=BORDER)
        select_panel.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        for i in range(4):
            select_panel.grid_columnconfigure(i, weight=1)

        self.btn_zips  = self._btn(select_panel, "Seleccionar ZIPs",    self.select_zips)
        self.btn_txt   = self._btn(select_panel, "Seleccionar TXT",     self.select_txt)
        self.btn_dest  = self._btn(select_panel, "Seleccionar destino", self.select_destination)
        self.btn_start = self._btn(select_panel, "Iniciar proceso",     self.start_processing, accent=True)
        self.btn_zips .grid(row=0, column=0, padx=12, pady=14, sticky="ew")
        self.btn_txt  .grid(row=0, column=1, padx=12, pady=14, sticky="ew")
        self.btn_dest .grid(row=0, column=2, padx=12, pady=14, sticky="ew")
        self.btn_start.grid(row=0, column=3, padx=12, pady=14, sticky="ew")

        # Centro: tabla + panel lateral
        center = ctk.CTkFrame(main, fg_color="transparent")
        center.grid(row=1, column=0, sticky="nsew")
        center.grid_columnconfigure(0, weight=3)
        center.grid_columnconfigure(1, weight=2)
        center.grid_rowconfigure(0, weight=1)

        # ── Tabla de trabajos ────────────────────────
        table_panel = ctk.CTkFrame(center, fg_color=PANEL, corner_radius=20,
                                   border_width=1, border_color=BORDER)
        table_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        table_panel.grid_rowconfigure(1, weight=1)
        table_panel.grid_columnconfigure(0, weight=1)

        table_hdr = ctk.CTkFrame(table_panel, fg_color=PANEL_2, corner_radius=14)
        table_hdr.grid(row=0, column=0, sticky="ew", padx=12, pady=12)
        for i in range(5):
            table_hdr.grid_columnconfigure(i, weight=1)

        for idx, h in enumerate(["ZIP", "ID", "Nombre final", "Progreso", "Estado"]):
            ctk.CTkLabel(table_hdr, text=h,
                         font=ctk.CTkFont(size=13, weight="bold"),
                         text_color=TEXT).grid(row=0, column=idx, padx=10, pady=10, sticky="w")

        self.table_scroll = ctk.CTkScrollableFrame(table_panel, fg_color="transparent", corner_radius=12)
        self.table_scroll.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        for i in range(5):
            self.table_scroll.grid_columnconfigure(i, weight=1)

        # ── Panel lateral ────────────────────────────
        side = ctk.CTkFrame(center, fg_color=PANEL, corner_radius=20,
                            border_width=1, border_color=BORDER)
        side.grid(row=0, column=1, sticky="nsew")
        side.grid_rowconfigure(2, weight=1)
        side.grid_columnconfigure(0, weight=1)

        self.summary_label = ctk.CTkLabel(
            side, text="Archivos: 0 | Procesados: 0 | Errores: 0",
            font=ctk.CTkFont(size=14, weight="bold"), text_color=TEXT,
        )
        self.summary_label.grid(row=0, column=0, padx=14, pady=(14, 8), sticky="w")

        self.global_progress = ctk.CTkProgressBar(
            side, height=18, corner_radius=8,
            fg_color=PANEL_2, progress_color=ACCENT)
        self.global_progress.grid(row=1, column=0, padx=14, pady=(0, 12), sticky="ew")
        self.global_progress.set(0)

        logs_frame = ctk.CTkFrame(side, fg_color=PANEL_2, corner_radius=16)
        logs_frame.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 14))
        logs_frame.grid_rowconfigure(1, weight=1)
        logs_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(logs_frame, text="Registro en tiempo real",
                     font=ctk.CTkFont(size=13, weight="bold"), text_color=TEXT,
                     ).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 8))

        self.log_text = ctk.CTkTextbox(
            logs_frame, wrap="word",
            fg_color=LOG_BG, text_color=TEXT, corner_radius=12)
        self.log_text.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.log_text.configure(state="disabled")

        # ── Footer ──────────────────────────────────
        footer = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=20,
                              border_width=1, border_color=BORDER)
        footer.grid(row=2, column=0, sticky="ew", padx=18, pady=(10, 18))
        footer.grid_columnconfigure(0, weight=1)
        footer.grid_columnconfigure(1, weight=1)
        footer.grid_columnconfigure(2, weight=1)

        self.status_label = ctk.CTkLabel(footer, text="Listo",
                                          font=ctk.CTkFont(size=13), text_color=MUTED)
        self.status_label.grid(row=0, column=0, padx=16, pady=16, sticky="w")

        self.workers_label = ctk.CTkLabel(footer, text="Modo: FIFO – 1 a la vez",
                                           font=ctk.CTkFont(size=13), text_color=MUTED)
        self.workers_label.grid(row=0, column=1, padx=16, pady=16)

        self.cancel_btn = self._btn(footer, "Cancelar", self.cancel_processing, width=180)
        self.cancel_btn.grid(row=0, column=2, padx=16, pady=16, sticky="e")
        self.cancel_btn.configure(state="disabled")

    def _btn(self, parent, text, command, accent=False, width=170):
        fg    = ACCENT    if accent else "#E2E8F0"
        hover = "#1D4ED8" if accent else "#CBD5E1"
        tc    = "white"   if accent else TEXT
        return ctk.CTkButton(
            parent, text=text, command=command,
            width=width, height=42, corner_radius=22,
            fg_color=fg, hover_color=hover,
            text_color=tc, font=ctk.CTkFont(size=13, weight="bold"),
        )

    # ──────────────────────────────────────────────
    # Selección de archivos
    # ──────────────────────────────────────────────
    def select_zips(self):
        if self.processing:
            messagebox.showwarning("En proceso", "No puedes cambiar la selección mientras está corriendo.")
            return
        files = filedialog.askopenfilenames(
            title="Selecciona archivos ZIP", filetypes=[("Archivos ZIP", "*.zip")])
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
            filetypes=[("Archivos TXT", "*.txt"), ("Todos", "*.*")])
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

    # ──────────────────────────────────────────────
    # Parseo del TXT
    # ──────────────────────────────────────────────
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
            id_m   = ID_REGEX.search(block)
            pref_m = PREF_REGEX.search(block)
            if id_m and pref_m:
                self.mapping[id_m.group(1)] = pref_m.group(1)

        self._log(f"[OK] Registros leídos del TXT: {len(self.mapping)}")
        if not self.mapping:
            self._log("[AVISO] No se encontraron pares ID / P-XXXXXX en el TXT.")

    # ──────────────────────────────────────────────
    # Tabla de trabajos
    # ──────────────────────────────────────────────
    def _extract_id_from_zip_name(self, zip_path: str):
        base = os.path.basename(zip_path).strip()
        m = re.search(r"mp_e57_(\d{8})", base, re.IGNORECASE)
        if m:
            return m.group(1)
        m = re.search(r"(\d{8})", base)
        return m.group(1) if m else None

    def _rebuild_jobs_preview(self):
        self._clear_table()
        self.jobs = []
        if not self.selected_zips:
            return
        for zip_path in self.selected_zips:
            id_value   = self._extract_id_from_zip_name(zip_path)
            prefix     = self.mapping.get(id_value, "SIN_PREFIJO") if id_value else "SIN_ID"
            final_name = (f"{prefix}_{id_value}"
                          if id_value and prefix != "SIN_PREFIJO"
                          else "SIN_CORRESPONDENCIA")
            output_path = (os.path.join(self.selected_destination, final_name)
                           if self.selected_destination else "")
            job = ZipJob(zip_path=zip_path, id_value=id_value or "",
                         prefix=prefix, final_name=final_name, output_path=output_path)
            self.jobs.append(job)
        for job in self.jobs:
            self._add_table_row(job)

    def _clear_table(self):
        for w in self.table_scroll.winfo_children():
            w.destroy()

    def _add_table_row(self, job: ZipJob):
        row = ctk.CTkFrame(self.table_scroll, fg_color=ROW_BG, corner_radius=14)
        row.pack(fill="x", pady=5, padx=2)
        row.grid_columnconfigure((0, 1, 2), weight=1)
        row.grid_columnconfigure(3, weight=2)
        row.grid_columnconfigure(4, weight=1)

        ctk.CTkLabel(row, text=os.path.basename(job.zip_path),
                     font=ctk.CTkFont(size=12), text_color=TEXT,
                     anchor="w").grid(row=0, column=0, padx=10, pady=10, sticky="w")

        ctk.CTkLabel(row, text=job.id_value or "No encontrado",
                     font=ctk.CTkFont(size=12), text_color=TEXT,
                     anchor="w").grid(row=0, column=1, padx=10, pady=10, sticky="w")

        ctk.CTkLabel(row, text=job.final_name,
                     font=ctk.CTkFont(size=12), text_color=TEXT,
                     anchor="w").grid(row=0, column=2, padx=10, pady=10, sticky="w")

        # Barra de progreso por archivo
        pb = ctk.CTkProgressBar(row, height=10, corner_radius=5,
                                 fg_color="#DDE4EF", progress_color=ACCENT)
        pb.grid(row=0, column=3, padx=10, pady=10, sticky="ew")
        pb.set(0)

        lbl_status = ctk.CTkLabel(row, text=job.status,
                                  font=ctk.CTkFont(size=12), text_color=MUTED, anchor="w")
        lbl_status.grid(row=0, column=4, padx=10, pady=10, sticky="w")

        # Guardar referencias para actualizaciones dinámicas
        job._ui_row    = row         # type: ignore[attr-defined]
        job._ui_pb     = pb          # type: ignore[attr-defined]
        job._ui_status = lbl_status  # type: ignore[attr-defined]

    def _refresh_row(self, job: ZipJob, progress: float = None):
        try:
            if progress is not None:
                job._ui_pb.set(progress)      # type: ignore[attr-defined]
            if job.status == "Completado":
                color = SUCCESS
            elif any(k in job.status for k in ("Error", "dañado", "inválido", "permisos", "SHA")):
                color = ERROR
            elif job.status in ("Extrayendo...", "Verificando ZIP...") or "/" in job.status:
                color = ACCENT
            else:
                color = MUTED
            job._ui_status.configure(text=job.status, text_color=color)  # type: ignore[attr-defined]
        except Exception:
            pass

    # ──────────────────────────────────────────────
    # Procesamiento
    # ──────────────────────────────────────────────
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
                messagebox.showerror(
                    "TXT vacío o inválido",
                    "No se encontraron coincidencias ID / P-XXXXXX en el TXT.")
                return

        self._rebuild_jobs_preview()

        missing = [j.zip_path for j in self.jobs if not j.id_value]
        if missing:
            messagebox.showwarning(
                "ZIP sin ID",
                "Algunos ZIPs no contienen un ID de 8 dígitos. Esos archivos no se procesarán correctamente.")

        self.processing = True
        self.cancel_event.clear()
        self.btn_start.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.status_label.configure(text="Procesando...")
        self._log("[INFO] Inicio de procesamiento (modo FIFO – un archivo a la vez).")

        self.worker_thread = threading.Thread(
            target=self._process_all, daemon=True)
        self.worker_thread.start()

    def _process_all(self):
        """
        Cola FIFO estricta: procesa los trabajos de uno en uno en el mismo
        orden en que fueron seleccionados. Un único hilo de fondo; la UI
        recibe actualizaciones vía ui_queue sin ningún bloqueo.
        """
        total  = len(self.jobs)
        done   = 0
        errors = 0

        self.ui_queue.put(("summary", total, done, errors))

        # Construir cola FIFO con todos los trabajos
        fifo: queue.Queue = queue.Queue()
        for job in self.jobs:
            fifo.put(job)

        while not fifo.empty():
            if self.cancel_event.is_set():
                break

            job = fifo.get()
            try:
                ok, message = self._process_single_job(job)
                if ok:
                    done += 1
                else:
                    errors += 1
                self.ui_queue.put(("job_result", job, ok, message))
            except Exception as e:
                errors += 1
                self.ui_queue.put(("job_result", job, False, f"Excepción: {e}"))

            processed = done + errors
            self.ui_queue.put(("progress",
                               processed / total if total else 0,
                               total, done, errors))

        if self.cancel_event.is_set():
            self.ui_queue.put(("done", False, "Proceso cancelado por el usuario."))
        else:
            self.ui_queue.put(("done", True, "Proceso terminado."))

    # ──────────────────────────────────────────────
    # Utilidades SHA-256
    # ──────────────────────────────────────────────
    @staticmethod
    def _sha256_file(path: str) -> str:
        """Calcula SHA-256 del archivo en disco (lectura por chunks)."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    # ──────────────────────────────────────────────
    # Extracción de un miembro con progreso + SHA-256
    # ──────────────────────────────────────────────
    def _extract_member_with_progress(
        self, zf, member, output_folder, job, file_idx: int, total_files: int
    ):
        """
        Extrae un miembro del ZIP haciendo streaming chunk a chunk.
        - Actualiza la UI con progreso real mientras escribe.
        - Calcula SHA-256 del stream descomprimido al vuelo.
        Devuelve (out_path, sha256_del_stream | None).
        """
        out_path = os.path.join(output_folder, member.filename)

        # Entrada de directorio → sólo crear
        if member.filename.endswith("/"):
            os.makedirs(out_path, exist_ok=True)
            return out_path, None

        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        file_size   = member.file_size          # tamaño descomprimido
        h_src       = hashlib.sha256()
        extracted   = 0
        last_update = 0

        with zf.open(member) as src, open(out_path, "wb") as dst:
            while True:
                chunk = src.read(65536)
                if not chunk:
                    break
                dst.write(chunk)
                h_src.update(chunk)
                extracted += len(chunk)

                # Actualizar UI: primer chunk o cada UPDATE_BYTES
                if last_update == 0 or (extracted - last_update) >= UPDATE_BYTES:
                    last_update = extracted
                    pct_file    = extracted / file_size if file_size > 0 else 1.0
                    self.ui_queue.put((
                        "file_progress",
                        job, file_idx, total_files,
                        os.path.basename(member.filename), pct_file,
                    ))

        return out_path, h_src.hexdigest()

    # ──────────────────────────────────────────────
    # Proceso de un trabajo individual
    # ──────────────────────────────────────────────
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

        final_name    = f"{prefix}_{job.id_value}"
        output_folder = os.path.join(self.selected_destination, final_name)
        job.final_name  = final_name
        job.output_path = output_folder

        try:
            os.makedirs(output_folder, exist_ok=True)

            with zipfile.ZipFile(job.zip_path, "r") as zf:

                # ── 1) Verificar integridad CRC del ZIP ──────────
                job.status = "Verificando ZIP..."
                self.ui_queue.put(("status_update", job))

                bad = zf.testzip()
                if bad is not None:
                    job.status = "ZIP dañado"
                    return False, f"ZIP corrupto, primer archivo con problema: {bad}"

                # ── 2) Extracción con progreso y SHA-256 ─────────
                members     = [m for m in zf.infolist() if not m.filename.endswith("/")]
                total_m     = len(members)
                sha_errors  = []

                job.status = "Extrayendo..."
                self.ui_queue.put(("status_update", job))

                for i, member in enumerate(members):
                    if self.cancel_event.is_set():
                        return False, "Cancelado durante extracción"

                    out_path, sha_src = self._extract_member_with_progress(
                        zf, member, output_folder, job, i + 1, total_m)

                    # ── 3) Validación SHA-256 ──────────────────────
                    if sha_src and os.path.isfile(out_path):
                        sha_dst = self._sha256_file(out_path)
                        name_short = os.path.basename(member.filename)

                        if sha_src != sha_dst:
                            sha_errors.append(member.filename)
                            self.ui_queue.put((
                                "log",
                                f"[ERROR] SHA-256 NO coincide: {name_short}\n"
                                f"        ZIP:   {sha_src}\n"
                                f"        Disco: {sha_dst}",
                            ))
                        else:
                            self.ui_queue.put((
                                "log",
                                f"[SHA-256 ✓] {name_short}  {sha_src}",
                            ))

            if sha_errors:
                job.status = "Error SHA-256"
                return False, f"Fallo de integridad SHA-256 en {len(sha_errors)} archivo(s)"

            # ── 4) Renombrar el archivo E57 = nombre de la carpeta ─
            e57_files = [
                f for f in os.listdir(output_folder)
                if f.lower().endswith(".e57")
                and os.path.isfile(os.path.join(output_folder, f))
            ]
            if e57_files:
                old_path = os.path.join(output_folder, e57_files[0])
                new_name = final_name + ".e57"
                new_path = os.path.join(output_folder, new_name)
                if old_path != new_path:
                    os.rename(old_path, new_path)
                self.ui_queue.put(("log", f"[OK] E57 renombrado → {new_name}"))
            else:
                self.ui_queue.put(("log", f"[AVISO] No se encontró archivo .e57 en {final_name}"))

            # ── NOTA: el ZIP original NO se copia dentro de la carpeta ─

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

    # ──────────────────────────────────────────────
    # Cola de mensajes → UI
    # ──────────────────────────────────────────────
    def _poll_ui_queue(self):
        try:
            while True:
                item = self.ui_queue.get_nowait()
                kind = item[0]

                if kind == "summary":
                    _, total, done, errors = item
                    self._update_summary(total, done, errors)

                elif kind == "progress":
                    _, progress, total, done, errors = item
                    self.global_progress.set(progress)
                    self._update_summary(total, done, errors)

                elif kind == "status_update":
                    _, job = item
                    self._refresh_row(job)

                elif kind == "file_progress":
                    # item = ("file_progress", job, file_idx, total_files, fname, pct_file)
                    _, job, file_idx, total_files, fname, pct_file = item
                    job_progress = (file_idx - 1 + pct_file) / total_files if total_files > 0 else 0
                    job.status = f"{file_idx}/{total_files} – {fname[:22]}"
                    self._refresh_row(job, progress=job_progress)

                elif kind == "job_result":
                    _, job, ok, message = item
                    if ok:
                        job.status = "Completado"
                    try:
                        job._ui_pb.set(1.0 if ok else 0.0)   # type: ignore[attr-defined]
                    except Exception:
                        pass
                    self._refresh_row(job)
                    tag = "[OK]" if ok else "[ERROR]"
                    self._log(f"{tag} {os.path.basename(job.zip_path)} → {message}")

                elif kind == "log":
                    _, message = item
                    self._log(message)

                elif kind == "done":
                    _, ok, message = item
                    self.processing = False
                    self.btn_start.configure(state="normal")
                    self.cancel_btn.configure(state="disabled")
                    self.status_label.configure(text="Listo" if ok else "Cancelado")
                    self._log(f"[INFO] {message}")

        except queue.Empty:
            pass

        self.after(120, self._poll_ui_queue)

    def _update_summary(self, total, done, errors):
        self.summary_label.configure(
            text=f"Archivos: {total} | Procesados: {done} | Errores: {errors}")

    def _refresh_summary(self):
        total  = len(self.jobs)
        done   = sum(1 for j in self.jobs if j.status == "Completado")
        errors = sum(1 for j in self.jobs
                     if j.status not in ("Pendiente", "Extrayendo...", "Completado", ""))
        self._update_summary(total, done, errors)

    def _log(self, message: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


# ──────────────────────────────────────────────────────────
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
