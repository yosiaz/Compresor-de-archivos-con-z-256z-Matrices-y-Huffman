import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# NUEVO — multiprocessing: la compresión corre en un PROCESO aparte (no en un hilo). Un hilo comparte
# el GIL con la ventana y, durante los bucles pesados, la congelaba; un proceso tiene su propio GIL.
import multiprocessing
# NUEVO — queue.Empty se usa al leer mensajes del proceso de trabajo sin bloquear la ventana
import queue
import os
import io
import contextlib
import motor

#Colores de la interfaz
AZUL_OSCURO = "#1F3864"     # color del texto principal
AZUL_MEDIO  = "#2E75B6"     # color de los títulos
GRIS_CLARO  = "#F5F5F5"     # fondo de las secciones
VERDE       = "#1a7f37"     # color de "RECONSTRUCCIÓN CORRECTA"
ROJO        = "#c0392b"     # color de errores
FUENTE      = ("Segoe UI", 10)
FUENTE_MONO = ("Courier New", 9)


# NUEVO — función que corre en el proceso de trabajo. Debe estar a nivel de módulo (requisito de "spawn").
# Envía mensajes a la ventana por la cola: ("progreso", 0..1), ("ok", resultado) o ("error", texto)
def _trabajo_proceso(operacion, args, cola):
    try:
        avisar = lambda fraccion: cola.put(("progreso", fraccion))   # NUEVO — callback de progreso para motor.py
        if operacion == "comprimir":
            resultado = motor.comprimir(args[0], args[1], progreso=avisar)
        else:
            resultado = motor.descomprimir(args[0], args[1], progreso=avisar)
        cola.put(("ok", resultado))
    except Exception as e:
        cola.put(("error", str(e)))


# VENTANA PRINCIPAL
class App:
    def __init__(self, raiz):
        self.raiz = raiz
        self.raiz.title("Compresor Matricial MTXC")
        self.raiz.geometry("580x520")
        self.raiz.resizable(False, False)
        self.raiz.configure(bg="white")

        #ESTADO INTERNO 
        # Guardamos las rutas para usarlas entre operaciones
        self.rutas_originales = []    # archivos que el usuario seleccionó
        self.ruta_mtxc        = None  # .mtxc resultado de comprimir

        # NUEVO — estado del proceso de trabajo
        self._ctx        = multiprocessing.get_context("spawn")  # NUEVO — "spawn" es seguro junto a tkinter
        self.proceso     = None    # NUEVO — proceso de trabajo en curso (o None)
        self.cola        = None    # NUEVO — cola por donde el proceso avisa progreso y resultado
        self._al_terminar = None   # NUEVO — función a llamar con el resultado
        self._titulo_error = ""    # NUEVO — título de la ventana de error
        self._texto_base  = ""     # NUEVO — texto de estado ("Comprimiendo…")
        self.raiz.protocol("WM_DELETE_WINDOW", self._al_cerrar)  # NUEVO — cerrar limpio si hay trabajo en curso

        #INTERFAZ 
        self._seccion_titulo()
        self._seccion_archivos()
        self._seccion_botones()
        self._seccion_progreso()
        self._seccion_resultados()
        # NUEVO — corrección: se quitó self._seccion_demo(); ese método no existe en el archivo y la ventana fallaba al abrir

#Encabezado
    def _seccion_titulo(self):
        marco = tk.Frame(self.raiz, bg=AZUL_OSCURO, height=60)
        marco.pack(fill="x")
        tk.Label(marco,
                 text="Compresor Matricial",
                 bg=AZUL_OSCURO, fg="white",
                 font=("Segoe UI", 12, "bold")
                 ).pack(pady=15)

    def _seccion_archivos(self):
        marco = tk.LabelFrame(self.raiz, text="Archivos",
                               bg="white", fg=AZUL_OSCURO,
                               font=("Segoe UI", 9, "bold"), padx=8, pady=6)
        marco.pack(fill="x", padx=12, pady=(10, 0))

        # etiqueta que muestra el nombre del archivo seleccionado
        self.lbl_archivos = tk.Label(marco, text="Ningún archivo seleccionado.",
                                      bg="white", fg="#555", font=FUENTE,
                                      anchor="w", wraplength=520)
        self.lbl_archivos.pack(fill="x")

    def _seccion_botones(self):
        marco = tk.Frame(self.raiz, bg="white", pady=8)
        marco.pack(fill="x", padx=12)

        estilo = {"font": FUENTE, "padx": 12, "pady": 6, "cursor": "hand2"}

        b1 = tk.Button(marco, text="Seleccionar archivos",
                       command=self.accion_seleccionar,
                       bg=AZUL_OSCURO, fg="white", **estilo)
        b1.pack(side="left", padx=(0, 6))

        b2 = tk.Button(marco, text="Comprimir",
                       command=self.accion_comprimir,
                       bg=AZUL_MEDIO, fg="white", **estilo)
        b2.pack(side="left", padx=(0, 6))

        b3 = tk.Button(marco, text="Descomprimir",
                       command=self.accion_descomprimir,
                       bg=AZUL_MEDIO, fg="white", **estilo)
        b3.pack(side="left", padx=(0, 6))
      

        # guardar referencias para poder deshabilitarlos mientras trabaja
        self._botones = [b1, b2, b3]

    def _seccion_progreso(self):
        marco = tk.Frame(self.raiz, bg="white")
        marco.pack(fill="x", padx=12, pady=(4, 0))

        self.barra = ttk.Progressbar(marco, mode="indeterminate", length=520)
        self.barra.pack(fill="x")

        self.lbl_estado = tk.Label(marco, text="", bg="white",
                                   fg="#555", font=("Segoe UI", 8))
        self.lbl_estado.pack(anchor="w", pady=(1, 0))

    def _seccion_resultados(self):
        """Sección donde se muestran las estadísticas de la última operación"""
        marco = tk.LabelFrame(self.raiz, text="Resultados",
                               bg="white", fg=AZUL_OSCURO,
                               font=("Segoe UI", 9, "bold"), padx=8, pady=6)
        marco.pack(fill="x", padx=12, pady=(4, 0))

        # etiquetas de estadísticas (se actualizan después de comprimir
        self.lbl_modo       = self._etiqueta_stat(marco, "Modo detectado:")
        self.lbl_original   = self._etiqueta_stat(marco, "Tamaño original:")
        self.lbl_comprimido = self._etiqueta_stat(marco, "Tamaño comprimido:")
        self.lbl_reduccion  = self._etiqueta_stat(marco, "Reducción:")
        self.lbl_matriz     = self._etiqueta_stat(marco, "Transformación:")
        self.lbl_integridad = tk.Label(marco, text="Reconstrucción: —",
                                        bg="white", fg="#555",
                                        font=("Segoe UI", 10, "bold"), anchor="w")
        self.lbl_integridad.pack(fill="x", pady=4)

    def _etiqueta_stat(self, padre, texto_fijo):
        fila = tk.Frame(padre, bg="white")
        fila.pack(fill="x", pady=1)
        tk.Label(fila, text=texto_fijo, bg="white", fg=AZUL_OSCURO,
                 font=("Segoe UI", 9, "bold"), width=20, anchor="w").pack(side="left")
        var = tk.Label(fila, text="—", bg="white", fg="#333", font=FUENTE, anchor="w")
        var.pack(side="left")
        return var


# ACCIONES (conectadas a los botones)
    def accion_seleccionar(self):
#Abre el cuadro de diálogo para seleccionar uno o más archivos.Acepta cualquier tipo de archivo
        rutas = filedialog.askopenfilenames(
            title="Seleccionar archivo(s) para comprimir",
            filetypes=[("Todos los archivos", "*.*")]
        )
        if not rutas:
            return    # el usuario cancelo

        self.rutas_originales = list(rutas)
        self.ruta_mtxc        = None    # limpiar .mtxc anterior

        # mostrar los nombres de archivos seleccionados
        nombres = ", ".join(os.path.basename(r) for r in rutas)
        self.lbl_archivos.config(text=nombres)
        self._limpiar_resultados()

    def accion_comprimir(self):
        #Comprime los archivos seleccionados en un hilo separado La barra de progreso se anima mientras trabaja
        if not self.rutas_originales:
            messagebox.showwarning("Sin archivos",
                                   "Primero selecciona al menos un archivo")
            return
        
        base           = os.path.splitext(self.rutas_originales[0])[0]
        self.ruta_mtxc = base + ".mtxc"
        self._iniciar_progreso("Comprimiendo…")
        # NUEVO — en vez de un hilo, se lanza un proceso aparte
        self._lanzar("comprimir", (self.rutas_originales, self.ruta_mtxc),
                     self._fin_comprimir, "Error al comprimir")

    def _fin_comprimir(self, stats):
        #Ejecutado en el hilo principal cuando la compresión termina
        self._detener_progreso()
        self._mostrar_stats_compresion(stats)

    def accion_descomprimir(self):
        
        #Descomprime un .mtxc en un hilo separado.La barra de progreso se anima mientras trabaja.
        
        ruta_mtxc = filedialog.askopenfilename(
            title="Seleccionar archivo .mtxc para descomprimir",
            filetypes=[("Archivos MTXC", "*.mtxc"), ("Todos", "*.*")]
        )
        if not ruta_mtxc:
            return

        carpeta = filedialog.askdirectory(title="¿Dónde guardar los archivos reconstruidos?")
        if not carpeta:
            return

        self._iniciar_progreso("Descomprimiendo…")
        # NUEVO — en vez de un hilo, se lanza un proceso aparte
        self._lanzar("descomprimir", (ruta_mtxc, carpeta),
                     self._fin_descomprimir, "Error al descomprimir")

    def _fin_descomprimir(self, rutas_generadas):
        #Ejecutado en el hilo principal cuando la descompresión termina
        self._detener_progreso()
        self._mostrar_resultado_descompresion(rutas_generadas)

    
    #progreso
    def _iniciar_progreso(self, texto):
    #inicia la barra animada y deshabilita los botones
        self._texto_base = texto                       # NUEVO — se recuerda para mostrar el porcentaje
        self.lbl_estado.config(text=texto)
        self.barra.config(mode="indeterminate")        # NUEVO — empieza animada hasta recibir el primer % real
        self.barra["value"] = 0
        self.barra.start(12)
        for b in self._botones:
            b.config(state="disabled")

    def _detener_progreso(self):
        #etiene la barra y reactiva los botones
        self.barra.stop()
        self.barra.config(mode="indeterminate")        # NUEVO — dejar la barra lista para la próxima operación
        self.barra["value"] = 0
        self.lbl_estado.config(text="")
        for b in self._botones:
            b.config(state="normal")

    # NUEVO — lanza el proceso de trabajo y empieza a revisar su cola cada 100 ms
    def _lanzar(self, operacion, args, al_terminar, titulo_error):
        self.cola          = self._ctx.Queue()
        self._al_terminar  = al_terminar
        self._titulo_error = titulo_error
        # NUEVO — daemon=False porque motor.py crea sus propios procesos hijos (pool) y un daemon no puede
        self.proceso = self._ctx.Process(target=_trabajo_proceso,
                                         args=(operacion, args, self.cola), daemon=False)
        self.proceso.start()
        self.raiz.after(100, self._revisar_cola)

    # NUEVO — pasa a barra con porcentaje real cuando llega el primer aviso de progreso
    def _actualizar_progreso(self, fraccion):
        if str(self.barra.cget("mode")) != "determinate":
            self.barra.stop()
            self.barra.config(mode="determinate", maximum=100)
        self.barra["value"] = fraccion * 100
        self.lbl_estado.config(text=f"{self._texto_base} {fraccion * 100:.0f}%")

    # NUEVO — lee los mensajes del proceso sin bloquear la ventana
    def _revisar_cola(self):
        final = None
        try:
            while final is None:
                tipo, dato = self.cola.get_nowait()
                if tipo == "progreso":
                    self._actualizar_progreso(dato)
                else:
                    final = (tipo, dato)
        except queue.Empty:
            pass

        if final is None and self.proceso is not None and not self.proceso.is_alive():
            # NUEVO — el proceso terminó: dar una última oportunidad a un mensaje aún en tránsito
            try:
                tipo, dato = self.cola.get(timeout=0.5)
                final = (tipo, dato) if tipo != "progreso" else ("error", "El proceso terminó sin resultado.")
            except queue.Empty:
                final = ("error", "El proceso de trabajo terminó inesperadamente.")

        if final is None:
            self.raiz.after(100, self._revisar_cola)    # NUEVO — seguir revisando
            return

        tipo, dato = final
        self.proceso.join(timeout=2)
        self.proceso, self.cola = None, None
        if tipo == "ok":
            self._al_terminar(dato)
        else:
            self._fin_error(self._titulo_error, dato)

    # NUEVO — al cerrar la ventana, detener el trabajo en curso
    def _al_cerrar(self):
        if self.proceso is not None and self.proceso.is_alive():
            self.proceso.terminate()
        self.raiz.destroy()

    def _fin_error(self, titulo, mensaje):
        self._detener_progreso()
        messagebox.showerror(titulo, mensaje)


    #visualizacion
    def _limpiar_resultados(self):
        #resetea todas las etiquetas de resultados a "—"."""
        for lbl in (self.lbl_modo, self.lbl_original, self.lbl_comprimido,
                    self.lbl_reduccion, self.lbl_matriz):
            lbl.config(text="—", fg="#333")
        self.lbl_integridad.config(text="Reconstrucción: —", fg="#555")

    def _mostrar_stats_compresion(self, stats):
        #actualiza las etiquetas con los resultados de la compresión."""
        MODOS = {0: "vacío", 1: "genérico (1D)",
                 2: "imagen BMP 2D", 3: "paquete de archivos",
                 5: "archivo grande por bloques (paralelo)",   # NUEVO — modo de bloques
                 6: "paquete grande por bloques (paralelo)"}   # NUEVO — paquete por bloques

        tam_o = stats["tam_original"]
        tam_c = stats["tam_comprimido"]

        self.lbl_modo.config(text=MODOS.get(stats["modo"], "?"))
        self.lbl_original.config(text=f"{tam_o:,} bytes")
        self.lbl_comprimido.config(text=f"{tam_c:,} bytes")

        if tam_o > 0:
            red = (1 - tam_c / tam_o) * 100
            if red < 0:
                self.lbl_reduccion.config(text=f"+{abs(red):.1f}% (creció)", fg=ROJO)
            else:
                self.lbl_reduccion.config(text=f"−{red:.1f}%", fg=VERDE)

        # información de la transformación matricial
        if stats.get("bloques"):                        # NUEVO — resumen para el modo por bloques
            self.lbl_matriz.config(
                text=f"D1 en {stats['bloques_d1']} de {stats['bloques']} bloques "
                     f"(H prom.: {stats['h_sin']:.2f}→{stats['h_con']:.2f} bits/byte)")
        elif "usa_d1" in stats:
            if stats["usa_d1"]:
                self.lbl_matriz.config(
                    text=f"D1 aplicada (H: {stats['h_sin']:.2f}→{stats['h_con']:.2f} bits/byte)")
            else:
                self.lbl_matriz.config(
                    text=f"Identidad (D1 no ayudaba: H {stats['h_sin']:.2f}<{stats['h_con']:.2f})")

        elif "usa_2d" in stats:
            if stats["usa_2d"]:
                self.lbl_matriz.config(
                    text=f"D·X·Dᵀ aplicada (H: {stats['h_sin']:.2f}→{stats['h_con']:.2f} bits/byte)",
                    fg=VERDE)
            else:
                self.lbl_matriz.config(
                    text=f"Identidad 2D (H sin={stats['h_sin']:.2f}, con={stats['h_con']:.2f})")

        if stats.get("almacenado"):
            self.lbl_matriz.config(text="Almacenado sin comprimir (compresión aumentaba el tamaño)")

        messagebox.showinfo("Listo",
                            f"Archivo comprimido guardado en:\n{self.ruta_mtxc}")

    def _mostrar_resultado_descompresion(self, rutas):
        """Muestra las rutas de los archivos reconstruidos."""
        self.lbl_integridad.config(
            text=f"Reconstrucción: {len(rutas)} archivo(s) generado(s)",
            fg=VERDE
        )
        messagebox.showinfo(
            "Listo",
            "Archivos reconstruidos:\n" + "\n".join(os.path.basename(r) for r in rutas)
        )

def main():
    """
    Crea la ventana principal de tkinter y la pone a correr.
    mainloop() mantiene la ventana abierta esperando clics del usuario.
    """
    multiprocessing.freeze_support()  # NUEVO — necesario si algún día se empaqueta como .exe (PyInstaller)
    motor.subir_prioridad()  # NUEVO — sube la prioridad del proceso una sola vez, antes de crear la ventana
    raiz = tk.Tk()
    App(raiz)                # construir la interfaz
    raiz.mainloop()          # esperar eventos (clics, teclas, cierre)


if __name__ == "__main__":
    main()