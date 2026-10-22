import struct
import heapq
import numpy as np
import os
from pathlib import Path

# NUEVO — sys se usa en subir_prioridad() para detectar el sistema
import sys as _sys
# NUEVO — gc permite pausar el recolector de basura mientras se comprime/descomprime
import gc as _gc
# NUEVO — ProcessPoolExecutor reparte archivos entre procesos (uno por núcleo) y evita el GIL
# (para os.cpu_count() se reutiliza el "import os" que ya existía arriba)
from concurrent.futures import ProcessPoolExecutor as _PPE
# NUEVO — deque se usa para mantener una ventana de bloques en vuelo (limita la RAM en archivos grandes)
from collections import deque as _deque


# NUEVO — función que sube la prioridad del proceso para que el OS le de más CPU, segun yo con esto disminuimos el tiempo de espera pero en las pruebas mostro que ayuda unicmente en el modo paquete
def subir_prioridad():
    """
    Sube la prioridad del proceso al máximo permitido sin permisos de administrador.
    En Windows usa SetPriorityClass (HIGH_PRIORITY_CLASS).
    En Linux/Mac usa os.nice(-10) (en algunos sistemas requiere permisos extra).
    Si falla silenciosamente, el programa sigue funcionando normal.
    """
    try:                                                    # NUEVO — nunca romper el programa por esto
        if _sys.platform == "win32":                        # NUEVO — rama de Windows
            import ctypes                                   # NUEVO — acceso a la API de Windows
            # NUEVO — HIGH_PRIORITY_CLASS = 0x80, aplicada al proceso actual
            ctypes.windll.kernel32.SetPriorityClass(
                ctypes.windll.kernel32.GetCurrentProcess(), 0x80
            )
        else:                                               # NUEVO — rama de Linux/Mac
            os.nice(-10)                                    # NUEVO — -10 = prioridad mayor
    except Exception:
        pass                                                # NUEVO — si no se puede, no pasa nada


#Constantes, N = para que la transformacion tenga efecto, Matrix de 8x8, B = Fotos e imagenes jpge, pixeles por bloque
N = 8
B = 8
FIRMA = b"MTXC"

# Le dicen al descompresor qué tipo de datos tiene el archivo
MODO_VACIO      = 0   # el archivo original estaba vacío
MODO_GENERICO   = 1   # texto, PDF, exe, cualquier cosa que no sea BMP 24-bit
MODO_IMAGEN     = 2   # imagen BMP 24-bit sin comprimir (transformación 2D)
MODO_PAQUETE    = 3   # varios archivos empacados en uno solo
MODO_ALMACENADO = 4   # archivo guardado tal cual (comprimir lo agrandaba)
# NUEVO — modo para archivos grandes: se parte en bloques independientes que se comprimen en paralelo
MODO_BLOQUES    = 5
# NUEVO — paquete con archivos grandes: cada archivo se parte en bloques y TODOS los bloques se reparten entre los núcleos
MODO_PAQUETE_BLOQUES = 6
# NUEVO — tamaño de cada bloque (múltiplo de N=8 para que los bloques de D1 queden alineados)
TAM_BLOQUE      = 8 * 1024 * 1024
# NUEVO — desde este tamaño (y si no es un BMP 24-bit) un archivo único usa MODO_BLOQUES
UMBRAL_BLOQUES  = 16 * 1024 * 1024


#1 Matrices
def construir_d1(n):
    D = np.eye(n, dtype=np.uint8)      # empieza como identidad
    for i in range(1, n):
        D[i, i - 1] = 255              # 255 = -1 mod 256, en la subdiagonal
    return D

def construir_d1_inversa(n):
     return np.tril(np.ones((n, n), dtype=np.uint8))

def verificar_inversa(n):
    D     = construir_d1(n)
    D_inv = construir_d1_inversa(n)
    I     = np.eye(n, dtype=np.uint8)
    return np.array_equal(D @ D_inv, I)

def aplicar_d1(X, D):
    return D @ X

def desaplicar_d1(Y, D_inv):
    return D_inv @ Y

def aplicar_d1_2d(bloque, D):
    return D @ bloque @ D.T    # primero filas (D@bloque), luego columnas (@D.T)

def desaplicar_d1_2d(bloque, D_inv):
    return D_inv @ bloque @ D_inv.T



#2 BLOQUES
"""Las matrices operan sobre vectores de tamaño N.
Estas funciones convierten los bytes del archivo en vectores,
y reconvierten los vectores a bytes al final."""

def bytes_a_bloques(datos, n):
    L = len(datos)
    if L == 0:
        return np.zeros((n, 0), dtype=np.uint8), 0

    M = -(-L // n)                                    # ceil(L / n)
    arreglo = np.zeros(M * n, dtype=np.uint8)
    arreglo[:L] = np.frombuffer(datos, dtype=np.uint8)
    return arreglo.reshape(M, n).T, L                 # .T → columnas = bloques


def bloques_a_bytes(X, L):
    return X.T.reshape(-1)[:L].astype(np.uint8).tobytes()



#3 Entropia
"""La entropía de Shannon mide qué tan comprimible es una secuencia
La usamos para decidir si D1 vale la pena en cada archivo concreto"""

def entropia(valores):
    if len(valores) == 0:
        return 0.0
    frec = np.bincount(valores.ravel().astype(np.uint8), minlength=256).astype(float)
    frec = frec[frec > 0]           # ignorar bytes que no aparecen
    p    = frec / frec.sum()        # convertir a probabilidades
    return float(-(p * np.log2(p)).sum())


#4: HUFFMAN

"""Huffman asigna códigos binarios de longitud variable a cada byte:
   los bytes más frecuentes reciben los códigos más cortos
   los menos frecuentes reciben los más largos
 Esto reduce el tamaño real en bytes"""

def _construir_codigos(frecuencias):
    if not frecuencias:
        return {}
    if len(frecuencias) == 1:
        # caso especial: solo hay un tipo de byte = código "0"
        return {list(frecuencias.keys())[0]: "0"}

    # el heap guarda: (frecuencia, id_único, símbolo_o_None, hijo_izq, hijo_der)
    # el id_único rompe empates sin comparar estructuras complejas
    heap = []
    for idx, (simbolo, frec) in enumerate(frecuencias.items()):
        heapq.heappush(heap, (frec, idx, simbolo, None, None))

    siguiente_id = len(frecuencias)
    while len(heap) > 1:
        # sacar los dos de menor frecuencia
        f1, _, s1, iz1, de1 = heapq.heappop(heap)
        f2, _, s2, iz2, de2 = heapq.heappop(heap)
        # crear nodo interno que los une
        nodo_iz = (f1, 0, s1, iz1, de1)
        nodo_de = (f2, 0, s2, iz2, de2)
        heapq.heappush(heap, (f1 + f2, siguiente_id, None, nodo_iz, nodo_de))
        siguiente_id += 1

    # recorrer el árbol completo para asignar un código a cada hoja
    codigos = {}
    pila = [(heap[0], "")]
    while pila:
        nodo, prefijo = pila.pop()
        f, _, simbolo, izq, der = nodo
        if simbolo is not None:
            codigos[simbolo] = prefijo or "0"   # hoja: guardar su código
        else:
            pila.append((izq, prefijo + "0"))   # bajar izquierda: añadir "0"
            pila.append((der, prefijo + "1"))   # bajar derecha: añadir "1"
    return codigos


def huffman_comprimir(valores):
    """Comprime un array de bytes usando codificación de Huffman

    PROCESO:
        1. Contar cuántas veces aparece cada byte (frecuencias)
        2. Construir el árbol y obtener los códigos
        3. Empacar los bits

    NUEVO — VERSIÓN VECTORIZADA CON NUMPY (mismo resultado, byte a byte, que la
    versión con bucle de Python; es entre 4 y 10 veces más rápida):
        - Cada símbolo se convierte a sus bits con una tabla (256 × longitud_máx)
          usando indexación de numpy, en vez de un bucle símbolo por símbolo.
        - Se descartan los bits sobrantes con una máscara y np.packbits junta
          los bits en bytes.
        - Se procesa por trozos para que la RAM no crezca con el tamaño de la
          entrada; los bits que no completan un byte se arrastran al trozo siguiente."""

    if len(valores) == 0:
        return b"", 0, {}

    # NUEVO — aplanar a uint8 sin copiar si ya lo es
    v = np.ascontiguousarray(valores.ravel(), dtype=np.uint8)

    # contar frecuencias de cada byte
    frec_arr    = np.bincount(v, minlength=256)
    frecuencias = {int(s): int(c) for s, c in enumerate(frec_arr) if c > 0}

    # construir los códigos del árbol de Huffman
    codigos = _construir_codigos(frecuencias)

    # NUEVO — tablas de numpy: longitud de cada código y sus bits (0/1) alineados a la izquierda
    max_lng = max(len(c) for c in codigos.values())
    lng_tab = np.zeros(256, dtype=np.uint8)
    bit_tab = np.zeros((256, max_lng), dtype=np.uint8)
    for simbolo, codigo in codigos.items():
        lng_tab[simbolo] = len(codigo)
        bit_tab[simbolo, :len(codigo)] = np.frombuffer(codigo.encode(), dtype=np.uint8) - 48  # '0'/'1' → 0/1
    columnas = np.arange(max_lng, dtype=np.uint8)

    # NUEVO — trozos de ~16 millones de elementos de matriz (acota la RAM temporal)
    paso    = max(1, (1 << 24) // max_lng)
    partes  = []                           # bytes ya empacados
    resto   = np.zeros(0, dtype=np.uint8)  # bits sueltos (<8) que pasan al siguiente trozo
    for i in range(0, len(v), paso):
        trozo = v[i:i + paso]
        mat   = bit_tab[trozo]                                    # (M, max_lng): bits de cada símbolo
        mask  = columnas[None, :] < lng_tab[trozo][:, None]       # True solo en los bits reales del código
        bits  = mat[mask]                                         # flujo de bits en orden (símbolo a símbolo)
        if len(resto):
            bits = np.concatenate([resto, bits])
        n8 = (len(bits) // 8) * 8                                 # bits que completan bytes
        partes.append(np.packbits(bits[:n8]))
        resto = bits[n8:]

    # rellenar el último byte parcial con ceros
    n_resto = len(resto)
    relleno = (8 - n_resto) % 8
    if n_resto:
        partes.append(np.packbits(np.concatenate([resto, np.zeros(relleno, dtype=np.uint8)])))

    return b"".join(p.tobytes() for p in partes), relleno, frecuencias


# NUEVO — decodificador rápido (mismo algoritmo de tabla de búsqueda, bucle más ajustado).
# Se usa cuando el código más largo cabe en la tabla (≤ 16 bits), que es el caso normal.
#   - Se sabe cuántos símbolos hay (total), así que no hace falta contar bits leídos.
#   - El acumulador se rellena de a 4 bytes y solo se recorta al rellenar.
#   - Es ~4 veces más rápido que el bucle genérico y produce exactamente la misma salida.
def _huffman_descomprimir_rapido(empacado, frecuencias, total, codigos, k):
    tsim = [0] * (1 << k)                      # NUEVO — símbolo para cada posible bloque de k bits
    tlen = [1] * (1 << k)                      # NUEVO — longitud del código para cada bloque
    for simbolo, codigo in codigos.items():
        lng  = len(codigo)
        base = (int(codigo, 2) if codigo else 0) << (k - lng)
        for extra in range(1 << (k - lng)):    # NUEVO — todas las entradas que comparten ese prefijo
            tsim[base | extra] = simbolo
            tlen[base | extra] = lng
    emp    = bytes(empacado) + b"\x00" * 8      # NUEVO — relleno para poder leer 4 bytes al final sin error
    salida = bytearray(total)
    acum, n_acum, pos = 0, 0, 0
    mascara = (1 << k) - 1
    desde_bytes = int.from_bytes
    for idx in range(total):
        if n_acum < k:                         # NUEVO — rellenar con 32 bits nuevos
            acum    = ((acum & ((1 << n_acum) - 1)) << 32) | desde_bytes(emp[pos:pos + 4], "big")
            n_acum += 32
            pos    += 4
        c          = (acum >> (n_acum - k)) & mascara   # NUEVO — los próximos k bits
        salida[idx] = tsim[c]
        n_acum    -= tlen[c]                   # NUEVO — consumir solo los bits del código
    return np.frombuffer(bytes(salida), dtype=np.uint8)


def huffman_descomprimir(empacado, relleno, frecuencias, total):
    """Descomprime bytes comprimidos con huffman_comprimir()

    VERSIÓN RÁPIDA: tabla de búsqueda + acumulador deslizante

    CÓMO FUNCIONA:
        Tabla de búsqueda: para cada posible bloque de K bits, la tabla
        guarda qué símbolo corresponde y cuántos bits consume. Un acceso
        a la tabla reemplaza recorrer el árbol bit a bit

        Acumulador deslizante: en vez de convertir todos los bytes
        comprimidos en un entero gigante, leemos el archivo comprimido
        de a un byte, manteniendo un acumulador que nunca supera ~24 bits
        Esto garantiza O(1) por símbolo y O(n) en total

    total: cuántos bytes hay que producir (guardado en el archivo)"""
    if total == 0:
        return np.array([], dtype=np.uint8)

    codigos  = _construir_codigos(frecuencias)
    KTABLA   = 16            # tabla de 2^16 = 65536 entradas
    max_bits = max((len(c) for c in codigos.values()), default=1)
    k        = min(max_bits, KTABLA)

    # NUEVO — camino rápido cuando todos los códigos caben en la tabla
    if max_bits <= KTABLA:
        return _huffman_descomprimir_rapido(empacado, frecuencias, total, codigos, k)

    # construir la tabla de búsqueda
    # tabla[chunk_de_k_bits] = (símbolo, longitud_del_código)
    tabla = [None] * (1 << k)
    for simbolo, codigo in codigos.items():
        lng = len(codigo)
        if lng > k:
            continue                         # códigos largos: los maneja el árbol
        val    = int(codigo, 2) if codigo else 0
        extras = 1 << (k - lng)             # todas las entradas con este prefijo
        base   = val << (k - lng)
        for extra in range(extras):
            tabla[base | extra] = (simbolo, lng)

    # árbol clásico solo para códigos más largos que k bits (raro en texto)
    raiz = None
    if any(len(c) > k for c in codigos.values()):
        class _N:
            __slots__ = ("i", "d", "v")
            def __init__(self): self.i = self.d = self.v = None
        raiz = _N()
        for simbolo, codigo in codigos.items():
            n = raiz
            for b in codigo:
                if b == "0":
                    if not n.i: n.i = _N()
                    n = n.i
                else:
                    if not n.d: n.d = _N()
                    n = n.d
            n.v = simbolo

    # acumulador deslizante: leemos el archivo comprimido byte a byte
    # y mantenemos un buffer de bits pequeño (máximo k+8 bits aprox.)
    salida    = np.empty(total, dtype=np.uint8)
    idx       = 0
    acum      = 0     # buffer deslizante de bits
    n_acum    = 0     # cuántos bits válidos hay en acum
    byte_idx  = 0     # próximo byte a leer del archivo comprimido
    n_bytes   = len(empacado)
    # total de bits útiles (ignorar el relleno del final)
    bits_utiles = n_bytes * 8 - relleno
    bits_leidos = 0   # bits ya consumidos del flujo

    while idx < total and bits_leidos < bits_utiles:
        # rellenar el acumulador hasta tener al menos k bits (o los que queden)
        while n_acum < k and byte_idx < n_bytes:
            acum    = (acum << 8) | empacado[byte_idx]
            n_acum += 8
            byte_idx += 1

        if n_acum == 0:
            break

        # extraer los primeros k bits del acumulador (o menos si estamos al final)
        disponibles = min(n_acum, bits_utiles - bits_leidos)
        leer        = min(k, disponibles)
        # alinear a k bits rellenando ceros a la derecha
        chunk = ((acum >> (n_acum - leer)) & ((1 << leer) - 1)) << (k - leer)

        entrada = tabla[chunk]
        if entrada is not None:
            simbolo, lng = entrada
            # consumir exactamente lng bits del acumulador
            n_acum      -= lng
            acum        &= (1 << n_acum) - 1
            bits_leidos += lng
            salida[idx]  = simbolo
            idx         += 1
        else:
            # código largo: árbol bit a bit (caso excepcional)
            nodo = raiz
            while nodo.v is None and bits_leidos < bits_utiles and n_acum > 0:
                bit         = (acum >> (n_acum - 1)) & 1
                n_acum     -= 1
                acum       &= (1 << n_acum) - 1
                bits_leidos += 1
                nodo        = nodo.i if bit == 0 else nodo.d
                # si vaciamos el acumulador, leer más bytes
                if n_acum == 0 and byte_idx < n_bytes:
                    acum    = empacado[byte_idx]
                    n_acum  = 8
                    byte_idx += 1
            if nodo is not None and nodo.v is not None:
                salida[idx] = nodo.v
                idx        += 1

    return salida[:idx]

#5: Lector/Escritor BPM, Solo en imagenes
def _leer_bmp(datos):
    """[Interna] Lee un BMP de 24 bits sin comprimir (BI_RGB).

    Retorna un dict con todos los campos del BMP, o None si el archivo
    no es un BMP de 24 bits compatible

    El dict contiene:
        encabezado:   bytes originales del encabezado (se guardan verbatim)
        ancho, alto:  dimensiones en píxeles
        relleno_fila: bytes de padding por fila (BMP alinea a múltiplos de 4)
        pad_datos:    los bytes de relleno reales (para reconstruir exacto)
        canales:      lista [B, G, R] de matrices numpy uint8 (alto×ancho)
        extra:        bytes tras los píxeles (normalmente vacío)
    """
    if len(datos) < 54 or datos[:2] != b"BM":
        return None                                     # no es BMP

    offset = struct.unpack_from("<I", datos, 10)[0]    # dónde empiezan los píxeles
    dib    = struct.unpack_from("<I", datos, 14)[0]    # tamaño del DIB header
    if dib != 40:
        return None                                     # solo BITMAPINFOHEADER

    ancho      = struct.unpack_from("<i", datos, 18)[0]
    alto_raw   = struct.unpack_from("<i", datos, 22)[0]
    bpp        = struct.unpack_from("<H", datos, 28)[0]
    compresion = struct.unpack_from("<I", datos, 30)[0]

    if bpp != 24 or compresion != 0:
        return None                                     # solo 24-bit sin comprimir

    alto   = abs(alto_raw)
    stride = ((ancho * 3 + 3) // 4) * 4               # ancho en bytes, alineado a 4
    pad_f  = stride - ancho * 3                        # bytes de relleno por fila

    pixeles = datos[offset: offset + stride * alto]
    extra   = datos[offset + stride * alto:]

    # separar los 3 canales de color
    canales   = [np.zeros((alto, ancho), dtype=np.uint8) for _ in range(3)]
    pad_bytes = bytearray()
    for f in range(alto):
        fila = pixeles[f * stride: f * stride + stride]
        px   = np.frombuffer(fila[:ancho * 3], dtype=np.uint8).reshape(ancho, 3)
        canales[0][f] = px[:, 0]    # azul
        canales[1][f] = px[:, 1]    # verde
        canales[2][f] = px[:, 2]    # rojo
        pad_bytes.extend(fila[ancho * 3:])

    return {
        "encabezado":  datos[:offset],
        "ancho":       ancho,
        "alto":        alto,
        "relleno_fila": pad_f,
        "pad_datos":   bytes(pad_bytes),
        "canales":     canales,
        "extra":       extra,}


def _escribir_bmp(info):
    ancho, alto = info["ancho"], info["alto"]
    pad_f       = info["relleno_fila"]
    pad_datos   = info["pad_datos"]
    canales     = info["canales"]

    filas = []
    for f in range(alto):
        px   = np.stack([canales[0][f], canales[1][f], canales[2][f]], axis=1)
        fila = px.astype(np.uint8).tobytes()
        if pad_f:
            fila += pad_datos[f * pad_f: (f + 1) * pad_f]
        filas.append(fila)

    return info["encabezado"] + b"".join(filas) + info["extra"]


#COMPRESOR CENTRAL
"""Estas funciones unen las secciones 1-5 para comprimir y descomprimir"""

def _comprimir_1d(datos):

    X, L = bytes_a_bloques(datos, N)

    D     = construir_d1(N)
    D_inv = construir_d1_inversa(N)
    Y     = aplicar_d1(X, D)           # Y = D·X  (mod 256)

    h_sin = entropia(X)                 # entropía sin transformar
    h_con = entropia(Y)                 # entropía con D1
    usa   = h_con < h_sin              # True si D1 ayuda

    vals            = Y.T.ravel() if usa else X.T.ravel()
    comp, rell, frec = huffman_comprimir(vals)

    return {
        "usa_d1": usa, "n": N, "L": L,
        "total":  len(vals),
        "relleno": rell, "frecuencias": frec, "comprimido": comp,
        "h_sin": h_sin, "h_con": h_con,
    }


def _descomprimir_1d(r):
    """
    [Interna] Inversa de _comprimir_1d(). Recibe el dict del registro.
    """
    vals = huffman_descomprimir(r["comprimido"], r["relleno"], r["frecuencias"], r["total"])
    M    = r["total"] // r["n"]
    Y    = vals.reshape(M, r["n"]).T    # reconstruir matriz de bloques

    if r["usa_d1"]:
        X = desaplicar_d1(Y, construir_d1_inversa(r["n"]))
    else:
        X = Y                           # no se transformó, Y ya es X

    return bloques_a_bytes(X, r["L"])


def _comprimir_imagen(info_bmp):
    D     = construir_d1(B)
    D_inv = construir_d1_inversa(B)
    alto  = info_bmp["alto"]
    ancho = info_bmp["ancho"]

    # dimensiones redondeadas al próximo múltiplo de B
    alto_r  = -(-alto  // B) * B
    ancho_r = -(-ancho // B) * B

    def canal_a_lista_bloques(canal):
        """Rellena el canal a (alto_r × ancho_r) y devuelve lista de bloques B×B."""
        lienzo = np.zeros((alto_r, ancho_r), dtype=np.uint8)
        lienzo[:alto, :ancho] = canal
        return [lienzo[i:i+B, j:j+B].copy()
                for i in range(0, alto_r, B)
                for j in range(0, ancho_r, B)]

    # recolectar todos los bloques de los 3 canales
    bloques = [canal_a_lista_bloques(c) for c in info_bmp["canales"]]

    # medir entropía sin y con transformación 2D
    vals_sin = np.concatenate([b.ravel() for canal in bloques for b in canal])
    vals_con = np.concatenate(
        [aplicar_d1_2d(b, D).ravel() for canal in bloques for b in canal]
    )
    h_sin = entropia(vals_sin)
    h_con = entropia(vals_con)
    usa   = h_con < h_sin

    vals_finales    = vals_con if usa else vals_sin
    comp, rell, frec = huffman_comprimir(vals_finales)

    return {
        "usa_2d": usa,
        "alto": alto, "ancho": ancho,
        "alto_r": alto_r, "ancho_r": ancho_r,
        "num_bloques": len(bloques[0]),     # bloques por canal
        "total":   len(vals_finales),
        "relleno": rell, "frecuencias": frec, "comprimido": comp,
        "h_sin": h_sin, "h_con": h_con,
    }


def _descomprimir_imagen(r, info_bmp_meta):
    D_inv = construir_d1_inversa(B)
    vals  = huffman_descomprimir(r["comprimido"], r["relleno"], r["frecuencias"], r["total"])

    alto_r  = r["alto_r"]
    ancho_r = r["ancho_r"]
    alto    = r["alto"]
    ancho   = r["ancho"]
    nb      = r["num_bloques"]          # bloques por canal

    # desempacar: vals está organizado como [canal0_bloque0, canal0_bloque1, ..., canal2_bloque_M]
    canales = []
    for c in range(3):
        segmento = vals[c * nb * B * B: (c + 1) * nb * B * B]
        bloques  = segmento.reshape(nb, B, B)

        # reconstruir el lienzo del canal
        lienzo   = np.zeros((alto_r, ancho_r), dtype=np.uint8)
        idx_blq  = 0
        for i in range(0, alto_r, B):
            for j in range(0, ancho_r, B):
                blk = bloques[idx_blq]
                if r["usa_2d"]:
                    blk = desaplicar_d1_2d(blk, D_inv)
                lienzo[i:i+B, j:j+B] = blk
                idx_blq += 1

        canales.append(lienzo[:alto, :ancho])

    return _escribir_bmp({**info_bmp_meta, "canales": canales})


#7: FORMATO .mtxc
# Definición del formato binario del archivo de salida.
#
# ESTRUCTURA GENERAL:
# [4 B] FIRMA    b"MTXC"
# [1 B] VERSION  1
# [1 B] MODO     0=vacío  1=genérico  2=imagen  3=paquete  4=almacenado  5=bloques  6=paquete por bloques
# [...] contenido según el modo
#
# REGISTRO GENÉRICO (usado en modo 1 y dentro de modo 3):
# [1 B] usa_d1     0 o 1
# [1 B] N          tamaño de bloque
# [4 B] L          longitud original
# [4 B] total      total de valores comprimidos
# [1 B] relleno    bits de relleno Huffman
# [1 B] K-1        número de símbolos en la tabla - 1
# [5×K B] tabla    {símbolo(1B) + frecuencia(4B)} × K
# [resto] flujo    bits Huffman empacados
#
# MODO 2 (imagen):
# [4 B] len_enc    longitud del encabezado BMP
# [?]   enc        encabezado BMP original
# [4 B] len_pad    longitud del bloque de padding de filas
# [?]   pad        padding de filas del BMP
# [4 B] len_ext    longitud de datos extra
# [?]   ext        datos extra del BMP
# [2 B] ancho      en píxeles
# [2 B] alto       en píxeles
# [1 B] relleno_f  bytes de relleno por fila
# [1 B] usa_2d     0 o 1
# [2 B] alto_r     alto redondeado a múltiplo de B
# [2 B] ancho_r    ancho redondeado a múltiplo de B
# [4 B] num_bloq   bloques por canal
# [4 B] total      total de valores comprimidos
# [1 B] relleno    bits de relleno Huffman
# [1 B] K-1
# [5×K B] tabla
# [resto] flujo

# MODO 6 (paquete por bloques, NUEVO): varios archivos, alguno grande
# [2 B] num_archivos
# num_archivos × ( [1 B] len_nombre  [?] nombre  [4 B] num_bloques
#                  + num_bloques × ( [4 B] len_registro + [?] REGISTRO GENÉRICO del bloque ) )
# Un archivo vacío tiene num_bloques = 0. Los bloques de cada archivo se concatenan en orden.

# MODO 5 (bloques, NUEVO): archivo único grande partido en bloques de TAM_BLOQUE
# [1 B] len_ext  [?] ext  [4 B] num_bloques
# num_bloques × ( [4 B] len_registro + [?] REGISTRO GENÉRICO del bloque )
# Los bloques se concatenan en orden para reconstruir el archivo original.

def _serializar_registro(r):
    K     = len(r["frecuencias"])
    tabla = b"".join(struct.pack("<BI", s, f) for s, f in r["frecuencias"].items())
    return (
        struct.pack("<BB II BB",
            int(r["usa_d1"]), r["n"],
            r["L"], r["total"],
            r["relleno"], K - 1)
        + tabla + r["comprimido"]
    )


def _deserializar_registro(datos, pos=0):
    usa_d1, n, L, total, relleno, K_menos_1 = struct.unpack_from("<BB II BB", datos, pos)
    pos += 12
    K    = K_menos_1 + 1
    frec = {}
    for _ in range(K):
        s, f  = struct.unpack_from("<BI", datos, pos)
        frec[s] = f
        pos    += 5
    comprimido = datos[pos:]
    return {"usa_d1": bool(usa_d1), "n": n, "L": L, "total": total,
            "relleno": relleno, "frecuencias": frec, "comprimido": comprimido}


# NUEVO — función auxiliar a nivel de módulo (ProcessPoolExecutor necesita poder serializarla con pickle)
def _comprimir_un_archivo(ruta_str):
    """
    Comprime un solo archivo y devuelve (nombre, registro_bytes).
    Se ejecuta en un proceso hijo separado del proceso principal.
    Llamada por comprimir() cuando hay varios archivos (MODO_PAQUETE).
    """
    ruta  = Path(ruta_str)                  # NUEVO — el hijo recibe la ruta como texto
    datos = ruta.read_bytes()               # NUEVO — cada proceso lee su propio archivo
    r     = _comprimir_1d(datos)            # NUEVO — misma lógica de siempre, sin cambios
    return ruta.name, _serializar_registro(r)


# NUEVO — avisar progreso (0.0 a 1.0) si quien llama pasó un callback; si no, no hace nada
def _avance(progreso, fraccion):
    if progreso is not None:
        progreso(min(1.0, max(0.0, fraccion)))


# NUEVO — ¿el archivo debe comprimirse por bloques? (grande y que NO sea un BMP 24-bit soportado)
def _usa_modo_bloques(ruta):
    if ruta.stat().st_size < UMBRAL_BLOQUES:
        return False
    with open(ruta, "rb") as f:
        cab = f.read(54)                      # NUEVO — solo se lee el encabezado, no el archivo entero
    if len(cab) >= 54 and cab[:2] == b"BM":
        dib  = struct.unpack_from("<I", cab, 14)[0]
        bpp  = struct.unpack_from("<H", cab, 28)[0]
        comp = struct.unpack_from("<I", cab, 30)[0]
        if dib == 40 and bpp == 24 and comp == 0:
            return False                      # NUEVO — BMP 24-bit: sigue por el modo IMAGEN de siempre
    return True


# NUEVO — comprime UN bloque del archivo (se ejecuta en un proceso hijo; debe estar a nivel de módulo)
def _comprimir_bloque(args):
    ruta_str, desde, largo = args
    with open(ruta_str, "rb") as f:           # NUEVO — cada proceso lee solo su trozo
        f.seek(desde)
        datos = f.read(largo)
    r = _comprimir_1d(datos)                  # NUEVO — misma lógica de siempre (D1 + Huffman)
    return _serializar_registro(r), r["usa_d1"], r["h_sin"], r["h_con"]


# NUEVO — descomprime UN bloque (proceso hijo); devuelve los bytes originales del bloque
def _descomprimir_bloque(registro_raw):
    return _descomprimir_1d(_deserializar_registro(registro_raw))


# NUEVO — ejecuta fn sobre cada elemento de 'trabajos' en un pool de procesos, devolviendo los
# resultados EN ORDEN pero sin tener más de 2×núcleos bloques en memoria a la vez
def _mapear_con_ventana(fn, trabajos, n_workers):
    ventana = n_workers * 2
    with _PPE(max_workers=n_workers) as pool:
        pendientes = _deque()
        it = iter(trabajos)
        hay_mas = True
        while pendientes or hay_mas:
            while hay_mas and len(pendientes) < ventana:
                try:
                    pendientes.append(pool.submit(fn, next(it)))
                except StopIteration:
                    hay_mas = False
            if pendientes:
                yield pendientes.popleft().result()


# NUEVO — compresión de un archivo grande: bloques de TAM_BLOQUE comprimidos en paralelo.
# Formato del cuerpo: [1B len_ext][ext][4B num_bloques] + num_bloques × ([4B len_registro][registro])
def _comprimir_en_bloques(ruta, ruta_salida, progreso):
    tam    = ruta.stat().st_size
    ext    = (ruta.suffix or ".bin").encode("utf-8")
    trozos = [(str(ruta), d, min(TAM_BLOQUE, tam - d)) for d in range(0, tam, TAM_BLOQUE)]
    n_workers = min(os.cpu_count() or 1, len(trozos))

    usa_d1_n, suma_h_sin, suma_h_con = 0, 0.0, 0.0
    with open(ruta_salida, "wb") as out:
        out.write(FIRMA + struct.pack("<BB", 1, MODO_BLOQUES))
        out.write(struct.pack("<B", len(ext)) + ext + struct.pack("<I", len(trozos)))
        for k, (registro, usa, h_sin, h_con) in enumerate(
                _mapear_con_ventana(_comprimir_bloque, trozos, n_workers), start=1):
            out.write(struct.pack("<I", len(registro)) + registro)   # NUEVO — se escribe apenas termina cada bloque
            usa_d1_n   += int(usa)
            suma_h_sin += h_sin
            suma_h_con += h_con
            _avance(progreso, k / len(trozos))
    n = len(trozos)
    return {"modo": MODO_BLOQUES, "tam_original": tam,
            "tam_comprimido": Path(ruta_salida).stat().st_size, "almacenado": False,
            "usa_d1": usa_d1_n > 0, "bloques": n, "bloques_d1": usa_d1_n,
            "h_sin": suma_h_sin / n, "h_con": suma_h_con / n}


# NUEVO — nombre de salida de un archivo del paquete; si dos se llaman igual, el segundo lleva _2, _3...
def _nombre_reconstruido(nombre, conteo_nombres):
    stem        = Path(nombre).stem
    sufijo      = Path(nombre).suffix or ".bin"
    nombre_base = stem + "_reconstruido" + sufijo
    if nombre_base not in conteo_nombres:
        conteo_nombres[nombre_base] = 1
        return nombre_base
    conteo_nombres[nombre_base] += 1
    return stem + f"_reconstruido_{conteo_nombres[nombre_base]}" + sufijo


# NUEVO — ¿hay que usar el paquete por bloques? (varios archivos y al menos uno grande)
def _usa_paquete_bloques(rutas):
    return len(rutas) > 1 and any(r.stat().st_size >= UMBRAL_BLOQUES for r in rutas)


# NUEVO — compresión de un paquete con archivos grandes. No carga los archivos en memoria: cada proceso
# lee su propio bloque. Todos los bloques de todos los archivos forman una sola cola de trabajo.
def _comprimir_paquete_bloques(rutas, ruta_salida, progreso):
    tamanos = [r.stat().st_size for r in rutas]
    nbloques = [(t + TAM_BLOQUE - 1) // TAM_BLOQUE for t in tamanos]     # NUEVO — bloques por archivo
    trabajos = [(str(r), d, min(TAM_BLOQUE, t - d))
                for r, t in zip(rutas, tamanos) for d in range(0, t, TAM_BLOQUE)]
    total     = len(trabajos)
    n_workers = min(os.cpu_count() or 1, total)
    resultados = _mapear_con_ventana(_comprimir_bloque, trabajos, n_workers)

    usa_n, suma_h_sin, suma_h_con, hechos = 0, 0.0, 0.0, 0
    with open(ruta_salida, "wb") as out:
        out.write(FIRMA + struct.pack("<BB", 1, MODO_PAQUETE_BLOQUES) + struct.pack("<H", len(rutas)))
        for ruta, nb in zip(rutas, nbloques):
            nombre_b = ruta.name.encode("utf-8")
            out.write(struct.pack("<B", len(nombre_b)) + nombre_b + struct.pack("<I", nb))
            for _ in range(nb):                                        # NUEVO — los resultados llegan en orden
                registro, usa, h_sin, h_con = next(resultados)
                out.write(struct.pack("<I", len(registro)) + registro)
                usa_n += int(usa); suma_h_sin += h_sin; suma_h_con += h_con
                hechos += 1
                _avance(progreso, hechos / total)
    return {"modo": MODO_PAQUETE_BLOQUES, "tam_original": sum(tamanos),
            "tam_comprimido": Path(ruta_salida).stat().st_size, "almacenado": False,
            "usa_d1": usa_n > 0, "bloques": total, "bloques_d1": usa_n,
            "h_sin": suma_h_sin / total, "h_con": suma_h_con / total}


#API PÚBLICA

def _comprimir_sin_gc(rutas_entrada, ruta_salida, progreso=None):  # NUEVO — antes se llamaba comprimir(); ahora lo envuelve comprimir()
    """
    Comprime uno o varios archivos a un único archivo .mtxc

    MODO AUTOMÁTICO:
         1 archivo BMP 24-bit sin comprimir = modo IMAGEN (D1 2D + Huffman)
         1 archivo de cualquier otro tipo   = modo GENÉRICO (D1 1D + Huffman)
         2 o más archivos                   = modo PAQUETE (genérico por archivo)

    Retorna: dict con estadísticas para mostrar en la interfaz
    """
    # normalizar: si se pasa una sola ruta como string, convertir a lista
    if isinstance(rutas_entrada, (str, Path)):
        rutas_entrada = [rutas_entrada]
    rutas_entrada = [Path(r) for r in rutas_entrada]

    # NUEVO — archivo único grande: modo por bloques (no se lee el archivo completo en memoria)
    if len(rutas_entrada) == 1 and _usa_modo_bloques(rutas_entrada[0]):
        return _comprimir_en_bloques(rutas_entrada[0], ruta_salida, progreso)

    # NUEVO — varios archivos y alguno grande: paquete por bloques (tampoco se cargan en memoria)
    if _usa_paquete_bloques(rutas_entrada):
        return _comprimir_paquete_bloques(rutas_entrada, ruta_salida, progreso)

    # leer todos los archivos
    contenidos = [r.read_bytes() for r in rutas_entrada]
    tam_original = sum(len(c) for c in contenidos)

    # determinar el modo
    if len(contenidos) == 0 or all(len(c) == 0 for c in contenidos):
        modo = MODO_VACIO

    elif len(contenidos) == 1:
        info_bmp = _leer_bmp(contenidos[0])
        if info_bmp is not None:
            modo = MODO_IMAGEN
        else:
            modo = MODO_GENERICO

    else:
        modo = MODO_PAQUETE

    # construir el cuerpo del archivo según el modo
    # NUEVO — corrección: struct.pack("<BB") necesita 2 valores (versión y modo); faltaba la versión (1)
    encabezado = FIRMA + struct.pack("<BB", 1, modo)
    stats      = {"modo": modo, "tam_original": tam_original}

    if modo == MODO_VACIO:
        cuerpo = b""

    elif modo == MODO_GENERICO:
        r     = _comprimir_1d(contenidos[0])
        # guardar la extensión original (ej: ".txt", ".pdf") para poder
        # reconstruirla al descomprimir. Sin esto, la salida siempre sería ".bin"
        ext   = (rutas_entrada[0].suffix or ".bin").encode("utf-8")
        cuerpo = struct.pack("<B", len(ext)) + ext + _serializar_registro(r)
        stats.update({"usa_d1": r["usa_d1"], "h_sin": r["h_sin"], "h_con": r["h_con"]})

    elif modo == MODO_IMAGEN:
        r_img = _comprimir_imagen(info_bmp)
        # encabezado BMP, padding y extra se guardan verbatim
        enc    = info_bmp["encabezado"]
        pad    = info_bmp["pad_datos"]
        ext    = info_bmp["extra"]
        tabla  = b"".join(struct.pack("<BI", s, f)
                           for s, f in r_img["frecuencias"].items())
        K      = len(r_img["frecuencias"])
        cuerpo = (
            struct.pack("<I", len(enc)) + enc +
            struct.pack("<I", len(pad)) + pad +
            struct.pack("<I", len(ext)) + ext +
            struct.pack("<HH B B HH I I BB",
                r_img["ancho"], r_img["alto"],
                info_bmp["relleno_fila"],
                int(r_img["usa_2d"]),
                r_img["alto_r"], r_img["ancho_r"],
                r_img["num_bloques"], r_img["total"],
                r_img["relleno"], K - 1)
            + tabla + r_img["comprimido"]
        )
        stats.update({"usa_2d": r_img["usa_2d"],
                      "h_sin": r_img["h_sin"], "h_con": r_img["h_con"]})

    else:  # MODO_PAQUETE
        # NUEVO — comprimir cada archivo en un proceso separado (un núcleo por archivo)
        n_workers = min(os.cpu_count() or 1, len(contenidos))   # NUEVO — nunca más procesos que archivos o núcleos
        partes = [struct.pack("<H", len(contenidos))]
        with _PPE(max_workers=n_workers) as pool:               # NUEVO — pool de procesos hijos
            # NUEVO — pool.map conserva el orden original de los archivos
            resultados = []
            for i, res in enumerate(pool.map(_comprimir_un_archivo,
                                             [str(r) for r in rutas_entrada]), start=1):
                resultados.append(res)                          # NUEVO — se recoge en orden y se avisa el progreso
                _avance(progreso, i / len(rutas_entrada))
        for nombre, registro in resultados:                     # NUEVO — armar el cuerpo igual que antes
            nombre_b = nombre.encode("utf-8")
            partes.append(
                struct.pack("<B", len(nombre_b)) + nombre_b +
                struct.pack("<I", len(registro)) + registro
            )
        cuerpo = b"".join(partes)

    # verificar si conviene comprimir o guardar tal cual.
    # Si el resultado comprimido es igual o más grande que el original + 6 bytes
    # de encabezado mínimo, no tiene sentido comprimir: guardar el archivo tal cual
    # con el modo ALMACENADO (solo se añaden 6 bytes de encabezado).
    contenido_final = encabezado + cuerpo
    if modo not in (MODO_VACIO, MODO_PAQUETE) and len(contenido_final) >= tam_original + 6:
        ext             = (rutas_entrada[0].suffix or ".bin").encode("utf-8")
        contenido_final = (FIRMA
                           + struct.pack("<BB", 1, MODO_ALMACENADO)   # NUEVO — corrección: se añade el byte de versión (1)
                           + struct.pack("<B", len(ext)) + ext
                           + contenidos[0])          # bytes originales, sin tocar
        stats["almacenado"] = True
    else:
        stats["almacenado"] = False

    Path(ruta_salida).write_bytes(contenido_final)
    stats["tam_comprimido"] = len(contenido_final)
    return stats


def _descomprimir_sin_gc(ruta_mtxc, carpeta_salida=None, progreso=None):  # NUEVO — antes se llamaba descomprimir(); ahora lo envuelve descomprimir()
    """
    Descomprime un archivo .mtxc y escribe los archivos originales.

    Si carpeta_salida es None, los archivos se escriben en el mismo
    directorio que el .mtxc, con el sufijo "_reconstruido".

    Retorna: lista de rutas de los archivos escritos
    """
    datos  = Path(ruta_mtxc).read_bytes()

    if datos[:4] != FIRMA:
        raise ValueError("El archivo no tiene la firma MTXC válida.")

    version = datos[4]
    modo    = datos[5]
    cuerpo  = datos[6:]

    if carpeta_salida is None:
        carpeta_salida = Path(ruta_mtxc).parent

    os.makedirs(carpeta_salida, exist_ok=True)
    archivos_escritos = []

    #modo almacenado (sin compresión
    if modo == MODO_ALMACENADO:
        ext_len = cuerpo[0]
        ext     = cuerpo[1: 1 + ext_len].decode("utf-8", errors="replace")
        datos   = cuerpo[1 + ext_len:]
        stem    = Path(ruta_mtxc).stem
        ruta    = Path(carpeta_salida) / (stem + "_reconstruido" + ext)
        ruta.write_bytes(datos)
        return [str(ruta)]

    #modo vacío
    if modo == MODO_VACIO:
        ruta = Path(carpeta_salida) / (Path(ruta_mtxc).stem + "_reconstruido.bin")
        ruta.write_bytes(b"")
        archivos_escritos.append(str(ruta))

    #modo genérico
    elif modo == MODO_GENERICO:
        ext_len   = cuerpo[0]
        ext       = cuerpo[1: 1 + ext_len].decode("utf-8", errors="replace")
        r         = _deserializar_registro(cuerpo[1 + ext_len:])
        recuperado = _descomprimir_1d(r)
        stem      = Path(ruta_mtxc).stem
        ruta      = Path(carpeta_salida) / (stem + ext)
        ruta.write_bytes(recuperado)
        archivos_escritos.append(str(ruta))

    #modo imagen
    elif modo == MODO_IMAGEN:
        pos = 0

        len_enc = struct.unpack_from("<I", cuerpo, pos)[0]; pos += 4
        enc     = cuerpo[pos: pos + len_enc];               pos += len_enc
        len_pad = struct.unpack_from("<I", cuerpo, pos)[0]; pos += 4
        pad     = cuerpo[pos: pos + len_pad];               pos += len_pad
        len_ext = struct.unpack_from("<I", cuerpo, pos)[0]; pos += 4
        ext     = cuerpo[pos: pos + len_ext];               pos += len_ext

        ancho, alto, rf, usa_2d, alto_r, ancho_r, num_b, total, rell, K_m1 = \
            struct.unpack_from("<HH B B HH I I BB", cuerpo, pos); pos += 20

        K    = K_m1 + 1
        frec = {}
        for _ in range(K):
            s, f  = struct.unpack_from("<BI", cuerpo, pos)
            frec[s] = f; pos += 5

        comprimido = cuerpo[pos:]
        r_img      = {"usa_2d": bool(usa_2d), "alto": alto, "ancho": ancho,
                      "alto_r": alto_r, "ancho_r": ancho_r, "num_bloques": num_b,
                      "total": total, "relleno": rell,
                      "frecuencias": frec, "comprimido": comprimido}
        info_meta  = {"encabezado": enc, "relleno_fila": rf,
                      "pad_datos": pad, "extra": ext,
                      "alto": alto, "ancho": ancho, "canales": None}
        recuperado = _descomprimir_imagen(r_img, info_meta)
        stem       = Path(ruta_mtxc).stem
        ruta       = Path(carpeta_salida) / (stem + "_reconstruido.bmp")
        ruta.write_bytes(recuperado)
        archivos_escritos.append(str(ruta))

    #modo paquete
    elif modo == MODO_PAQUETE:
        pos       = 0
        num       = struct.unpack_from("<H", cuerpo, pos)[0]; pos += 2
        # contador para resolver conflictos de nombre:
        # si dos archivos se llaman "nota.txt", el segundo sale como "nota_reconstruido_2.txt"
        conteo_nombres = {}
        for _ in range(num):
            ln      = cuerpo[pos];                        pos += 1
            nombre  = cuerpo[pos: pos + ln].decode();    pos += ln
            lr      = struct.unpack_from("<I", cuerpo, pos)[0]; pos += 4
            reg_raw = cuerpo[pos: pos + lr];             pos += lr

            r          = _deserializar_registro(reg_raw)
            recuperado = _descomprimir_1d(r)
            stem       = Path(nombre).stem
            sufijo     = Path(nombre).suffix or ".bin"
            nombre_base = stem + "_reconstruido" + sufijo

            if nombre_base not in conteo_nombres:
                conteo_nombres[nombre_base] = 1
                nombre_final = nombre_base
            else:
                conteo_nombres[nombre_base] += 1
                n = conteo_nombres[nombre_base]
                nombre_final = stem + f"_reconstruido_{n}" + sufijo

            ruta = Path(carpeta_salida) / nombre_final
            ruta.write_bytes(recuperado)
            archivos_escritos.append(str(ruta))
            _avance(progreso, len(archivos_escritos) / num)   # NUEVO — avisar progreso por archivo

    # NUEVO — paquete por bloques: todos los bloques de todos los archivos se descomprimen en paralelo
    elif modo == MODO_PAQUETE_BLOQUES:
        pos = 0
        num = struct.unpack_from("<H", cuerpo, pos)[0]; pos += 2
        archivos = []                                     # NUEVO — (nombre, [(pos, largo) de cada bloque])
        for _ in range(num):
            ln     = cuerpo[pos];                                  pos += 1
            nombre = cuerpo[pos: pos + ln].decode("utf-8");        pos += ln
            nb     = struct.unpack_from("<I", cuerpo, pos)[0];     pos += 4
            bloques = []
            for _ in range(nb):
                lr = struct.unpack_from("<I", cuerpo, pos)[0];     pos += 4
                bloques.append((pos, lr))
                pos += lr
            archivos.append((nombre, bloques))
        total     = sum(len(b) for _, b in archivos)
        n_workers = min(os.cpu_count() or 1, max(1, total))
        trabajos  = (bytes(cuerpo[p: p + lr]) for _, bl in archivos for p, lr in bl)
        resultados = _mapear_con_ventana(_descomprimir_bloque, trabajos, n_workers)
        conteo_nombres, hechos = {}, 0
        for nombre, bloques in archivos:
            ruta = Path(carpeta_salida) / _nombre_reconstruido(nombre, conteo_nombres)
            with open(ruta, "wb") as out:
                for _ in bloques:
                    out.write(next(resultados))                # NUEVO — escritura en orden, sin acumular en RAM
                    hechos += 1
                    _avance(progreso, hechos / total)
            archivos_escritos.append(str(ruta))

    # NUEVO — modo bloques: cada bloque se descomprime en un proceso aparte y se escribe en orden
    elif modo == MODO_BLOQUES:
        ext_len = cuerpo[0]
        ext     = cuerpo[1: 1 + ext_len].decode("utf-8", errors="replace")
        pos     = 1 + ext_len
        num     = struct.unpack_from("<I", cuerpo, pos)[0]; pos += 4
        registros = []                                    # NUEVO — (posición, largo) de cada registro
        for _ in range(num):
            lr = struct.unpack_from("<I", cuerpo, pos)[0]; pos += 4
            registros.append((pos, lr))
            pos += lr
        ruta = Path(carpeta_salida) / (Path(ruta_mtxc).stem + "_reconstruido" + ext)
        n_workers = min(os.cpu_count() or 1, max(1, num))
        trabajos  = (bytes(cuerpo[p: p + lr]) for p, lr in registros)   # NUEVO — se generan bajo demanda
        with open(ruta, "wb") as out:
            for k, trozo in enumerate(
                    _mapear_con_ventana(_descomprimir_bloque, trabajos, n_workers), start=1):
                out.write(trozo)                          # NUEVO — escritura en orden, sin acumular en RAM
                _avance(progreso, k / num)
        archivos_escritos.append(str(ruta))

    return archivos_escritos


# NUEVO — comprimir() público: pausa el GC durante todo el trabajo pesado y lo reactiva siempre
def comprimir(rutas_entrada, ruta_salida, progreso=None):
    """Misma función de siempre (ver _comprimir_sin_gc), pero con el GC pausado.
    NUEVO — progreso: función opcional que recibe un número de 0.0 a 1.0."""
    gc_estaba_activo = _gc.isenabled()      # NUEVO — recordar el estado previo del GC
    _gc.disable()                           # NUEVO — pausar el GC: evita pausas aleatorias en el loop de Huffman
    try:
        return _comprimir_sin_gc(rutas_entrada, ruta_salida, progreso)
    finally:
        if gc_estaba_activo:                # NUEVO — siempre reactivar, aunque haya error
            _gc.enable()


# NUEVO — descomprimir() público: igual, con el GC pausado durante _descomprimir_1d/_imagen/huffman_descomprimir
def descomprimir(ruta_mtxc, carpeta_salida=None, progreso=None):
    """Misma función de siempre (ver _descomprimir_sin_gc), pero con el GC pausado.
    NUEVO — progreso: función opcional que recibe un número de 0.0 a 1.0."""
    gc_estaba_activo = _gc.isenabled()      # NUEVO — recordar el estado previo del GC
    _gc.disable()                           # NUEVO — pausar el GC durante la descompresión
    try:
        return _descomprimir_sin_gc(ruta_mtxc, carpeta_salida, progreso)
    finally:
        if gc_estaba_activo:                # NUEVO — siempre reactivar, aunque haya error
            _gc.enable()