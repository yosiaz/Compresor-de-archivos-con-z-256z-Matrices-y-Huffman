
# tkinter: biblioteca estándar de Python para crear interfaces gráficas.
#   Viene incluida con Python; no necesita instalación separada.
#   filedialog: para abrir el cuadro "Seleccionar archivo"
#   messagebox: para mostrar ventanas de error o confirmación
#   scrolledtext: cuadro de texto con barra de desplazamiento (para la demo)
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

# os.path: para extraer el nombre de archivo de una ruta completa
import os

# io y contextlib: para capturar lo que motor.demo() imprime en consola
#   y mostrarlo en la ventana gráfica en vez de la terminal
import io
import contextlib

# motor: importamos TODO el motor de compresión.
#   La GUI solo usa: motor.comprimir(), motor.descomprimir(), motor.demo()
import motor


# ══════════════════════════════════════════════════════════════════════
# CONSTANTES DE ESTILO
# ══════════════════════════════════════════════════════════════════════

AZUL_OSCURO = "#1F3864"     # color del texto principal
AZUL_MEDIO  = "#2E75B6"     # color de los títulos
GRIS_CLARO  = "#F5F5F5"     # fondo de las secciones
VERDE       = "#1a7f37"     # color de "RECONSTRUCCIÓN CORRECTA"
ROJO        = "#c0392b"     # color de errores
FUENTE      = ("Segoe UI", 10)
FUENTE_MONO = ("Courier New", 9)


# ══════════════════════════════════════════════════════════════════════
# VENTANA PRINCIPAL
# ══════════════════════════════════════════════════════════════════════

class App:
    """
    La ventana principal del programa.

    ¿POR QUÉ UNA CLASE?
        Usando una clase podemos guardar estado entre clicks
        (por ejemplo, recordar qué archivo se seleccionó para comprimir,
        y usarlo cuando el usuario hace click en "Descomprimir").
        Sin la clase, necesitaríamos variables globales (más desordenado).
    """

    def __init__(self, raiz):
        """
        Construye la ventana: título, secciones, botones y etiquetas.
        raiz: la ventana de tkinter (tk.Tk()) que nos pasarán desde main().
        """
        self.raiz = raiz
        self.raiz.title("Compresor Matricial MTXC")
        self.raiz.geometry("580x520")
        self.raiz.resizable(False, False)
        self.raiz.configure(bg="white")

        # ── ESTADO INTERNO ────────────────────────────────────────
        # Guardamos las rutas para usarlas entre operaciones
        self.rutas_originales = []    # archivos que el usuario seleccionó
        self.ruta_mtxc        = None  # .mtxc resultado de comprimir

        # ── CONSTRUCCIÓN DE LA INTERFAZ ───────────────────────────
        self._seccion_titulo()
        self._seccion_archivos()
        self._seccion_botones()
        self._seccion_resultados()
        self._seccion_demo()

    def _seccion_titulo(self):
        """Encabezado con el nombre del proyecto."""
        marco = tk.Frame(self.raiz, bg=AZUL_OSCURO, height=60)
        marco.pack(fill="x")
        tk.Label(marco,
                 text="Compresor Matricial MTXC  ·  Y = A·X (mod 256)",
                 bg=AZUL_OSCURO, fg="white",
                 font=("Segoe UI", 12, "bold")
                 ).pack(pady=15)

    def _seccion_archivos(self):
        """Sección donde se muestran los archivos seleccionados."""
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
        """Fila de botones de acción."""
        marco = tk.Frame(self.raiz, bg="white", pady=8)
        marco.pack(fill="x", padx=12)

        estilo = {"font": FUENTE, "padx": 12, "pady": 6, "cursor": "hand2"}

        # Seleccionar archivo(s): abre el cuadro de diálogo de selección
        tk.Button(marco, text="📂  Seleccionar archivo(s)",
                  command=self.accion_seleccionar,
                  bg=AZUL_OSCURO, fg="white", **estilo
                  ).pack(side="left", padx=(0, 6))

        # Comprimir: llama a motor.comprimir() con los archivos seleccionados
        tk.Button(marco, text="⬇  Comprimir",
                  command=self.accion_comprimir,
                  bg=AZUL_MEDIO, fg="white", **estilo
                  ).pack(side="left", padx=(0, 6))

        # Descomprimir: abre un .mtxc y llama a motor.descomprimir()
        tk.Button(marco, text="⬆  Descomprimir",
                  command=self.accion_descomprimir,
                  bg=AZUL_MEDIO, fg="white", **estilo
                  ).pack(side="left", padx=(0, 6))

        # Demo: muestra las matrices en una ventana aparte
        tk.Button(marco, text="📐  Demo N=4",
                  command=self.accion_demo,
                  bg="#555", fg="white", **estilo
                  ).pack(side="left")

    def _seccion_resultados(self):
        """Sección donde se muestran las estadísticas de la última operación."""
        marco = tk.LabelFrame(self.raiz, text="Resultados",
                               bg="white", fg=AZUL_OSCURO,
                               font=("Segoe UI", 9, "bold"), padx=8, pady=6)
        marco.pack(fill="x", padx=12, pady=(4, 0))

        # etiquetas de estadísticas (se actualizan después de comprimir)
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
        """
        [Auxiliar] Crea un par "Label fijo: Label variable" en una fila.
        El label variable se actualiza con los resultados.
        """
        fila = tk.Frame(padre, bg="white")
        fila.pack(fill="x", pady=1)
        tk.Label(fila, text=texto_fijo, bg="white", fg=AZUL_OSCURO,
                 font=("Segoe UI", 9, "bold"), width=20, anchor="w").pack(side="left")
        var = tk.Label(fila, text="—", bg="white", fg="#333", font=FUENTE, anchor="w")
        var.pack(side="left")
        return var

    def _seccion_demo(self):
        """Pie de la ventana con atajo rápido a la demo."""
        pie = tk.Frame(self.raiz, bg=GRIS_CLARO, height=30)
        pie.pack(fill="x", side="bottom")
        tk.Label(pie,
                 text="Tip: 'Demo N=4' muestra las matrices D1 y D1⁻¹ con un ejemplo a mano",
                 bg=GRIS_CLARO, fg="#777", font=("Segoe UI", 8)
                 ).pack(pady=6)

    # ══════════════════════════════════════════════════════════════
    # ACCIONES (conectadas a los botones)
    # ══════════════════════════════════════════════════════════════

    def accion_seleccionar(self):
        """
        Abre el cuadro de diálogo para seleccionar uno o más archivos.
        Acepta cualquier tipo de archivo.
        """
        rutas = filedialog.askopenfilenames(
            title="Seleccionar archivo(s) para comprimir",
            filetypes=[("Todos los archivos", "*.*")]
        )
        if not rutas:
            return    # el usuario canceló

        self.rutas_originales = list(rutas)
        self.ruta_mtxc        = None    # limpiar .mtxc anterior

        # mostrar los nombres de archivos seleccionados (no la ruta completa)
        nombres = ", ".join(os.path.basename(r) for r in rutas)
        self.lbl_archivos.config(text=nombres)
        self._limpiar_resultados()

    def accion_comprimir(self):
        """
        Comprime los archivos seleccionados.
        Llama a motor.comprimir() y muestra los resultados.
        """
        if not self.rutas_originales:
            messagebox.showwarning("Sin archivos",
                                   "Primero selecciona al menos un archivo.")
            return

        # determinar ruta de salida (.mtxc)
        base          = os.path.splitext(self.rutas_originales[0])[0]
        self.ruta_mtxc = base + ".mtxc"

        # mostrar cursor de espera mientras trabaja
        self.raiz.config(cursor="watch")
        self.raiz.update()

        try:
            # toda la matemática ocurre en este llamado
            stats = motor.comprimir(self.rutas_originales, self.ruta_mtxc)
            self._mostrar_stats_compresion(stats)
        except Exception as e:
            messagebox.showerror("Error al comprimir", str(e))
        finally:
            self.raiz.config(cursor="")    # restaurar cursor

    def accion_descomprimir(self):
        """
        Abre un cuadro de diálogo para seleccionar un .mtxc y lo descomprime.
        Verifica la integridad si el archivo original también está disponible.
        """
        ruta_mtxc = filedialog.askopenfilename(
            title="Seleccionar archivo .mtxc para descomprimir",
            filetypes=[("Archivos MTXC", "*.mtxc"), ("Todos", "*.*")]
        )
        if not ruta_mtxc:
            return

        carpeta = filedialog.askdirectory(title="¿Dónde guardar los archivos reconstruidos?")
        if not carpeta:
            return

        self.raiz.config(cursor="watch")
        self.raiz.update()

        try:
            # toda la matemática inversa ocurre en este llamado
            rutas_generadas = motor.descomprimir(ruta_mtxc, carpeta)
            self._mostrar_resultado_descompresion(rutas_generadas)
        except Exception as e:
            messagebox.showerror("Error al descomprimir", str(e))
        finally:
            self.raiz.config(cursor="")

    def accion_demo(self):
        """
        Abre una nueva ventana con el demo de matrices (N=4).
        Captura la salida de motor.demo() y la muestra en un cuadro de texto.
        """
        # capturar lo que motor.demo() imprimiría en la terminal
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            motor.demo(4)
        texto_demo = buffer.getvalue()

        # crear ventana emergente
        ventana = tk.Toplevel(self.raiz)
        ventana.title("Demo de matrices (N=4)")
        ventana.geometry("560x420")
        ventana.configure(bg="white")

        tk.Label(ventana,
                 text="Matrices D1 y D1⁻¹  ·  Ejemplo Y=D1·x  ·  Verificación D1·D1⁻¹=I",
                 bg=AZUL_OSCURO, fg="white", font=("Segoe UI", 9, "bold")
                 ).pack(fill="x", pady=0)

        # cuadro de texto con la salida del demo
        txt = scrolledtext.ScrolledText(ventana, font=FUENTE_MONO,
                                         bg="#f8f9fa", wrap="none",
                                         padx=10, pady=10)
        txt.pack(fill="both", expand=True, padx=8, pady=8)
        txt.insert("1.0", texto_demo)
        txt.config(state="disabled")    # solo lectura

    # ══════════════════════════════════════════════════════════════
    # HELPERS DE VISUALIZACIÓN
    # ══════════════════════════════════════════════════════════════

    def _limpiar_resultados(self):
        """Resetea todas las etiquetas de resultados a "—"."""
        for lbl in (self.lbl_modo, self.lbl_original, self.lbl_comprimido,
                    self.lbl_reduccion, self.lbl_matriz):
            lbl.config(text="—", fg="#333")
        self.lbl_integridad.config(text="Reconstrucción: —", fg="#555")

    def _mostrar_stats_compresion(self, stats):
        """Actualiza las etiquetas con los resultados de la compresión."""
        MODOS = {0: "vacío", 1: "genérico (1D)",
                 2: "imagen BMP 2D", 3: "paquete de archivos"}

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
        if "usa_d1" in stats:
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


# ══════════════════════════════════════════════════════════════════════
# PUNTO DE ENTRADA
# ══════════════════════════════════════════════════════════════════════

def main():
    """
    Crea la ventana principal de tkinter y la pone a correr.
    mainloop() mantiene la ventana abierta esperando clics del usuario.
    """
    raiz = tk.Tk()
    App(raiz)                # construir la interfaz
    raiz.mainloop()          # esperar eventos (clics, teclas, cierre)


if __name__ == "__main__":
    main()
