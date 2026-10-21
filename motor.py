import struct
import heapq
import numpy as np
import os
from pathlib import Path


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
        3. Empacar los bits en un bytearray usando un acumulador pequeño
           que se vacía byte a byte (nunca crece más de ~30 bits)

    POR QUÉ UN ACUMULADOR PEQUEÑO Y NO UN ENTERO GIGANTE:
        Si acumulamos todos los bits en un solo entero Python, ese entero
        puede llegar a tener millones de bits para archivos grandes, y las
        operaciones de shift sobre él se vuelven cada vez más lentas (O(n**2)
        en total). Con un buffer de bytes que se vacía byte a byte, el
        acumulador nunca supera los 32 bits → cada operación es O(1)"""
    
    if len(valores) == 0:
        return b"", 0, {}

    # contar frecuencias de cada byte
    frec_arr    = np.bincount(valores.ravel().astype(np.uint8), minlength=256)
    frecuencias = {int(v): int(c) for v, c in enumerate(frec_arr) if c > 0}

    # construir los códigos del árbol de Huffman
    codigos = _construir_codigos(frecuencias)

    # pre-convertir cada código a (entero, longitud) para evitar int() dentro del loop
    tabla = [(0, 0)] * 256
    for simbolo, codigo in codigos.items():
        tabla[simbolo] = (int(codigo, 2) if codigo else 0, len(codigo))

    # empacar bits en un bytearray usando un acumulador pequeño:
    #   acum acumula bits de izquierda a derecha
    #   cada vez que tiene 8 bits completos, los vuelca al buffer y los descarta
    buf    = bytearray()
    acum   = 0     # acumulador: nunca supera los 32 bits aproximadamente
    n_acum = 0     # cuántos bits hay en acum ahora mismo

    for v in valores.ravel():
        val, lng = tabla[int(v)]
        acum     = (acum << lng) | val   # añadir el código al acumulador
        n_acum  += lng
        # volcar tantos bytes completos como haya
        while n_acum >= 8:
            n_acum -= 8
            buf.append((acum >> n_acum) & 0xFF)  # el byte más significativo
            acum &= (1 << n_acum) - 1             # descartar lo que ya volcamos

    # rellenar el último byte parcial con ceros
    relleno = (8 - n_acum) % 8
    if n_acum > 0:
        buf.append((acum << relleno) & 0xFF)

    return bytes(buf), relleno, frecuencias


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
# [1 B] MODO     0=vacío  1=genérico  2=imagen  3=paquete
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


#API PÚBLICA

def comprimir(rutas_entrada, ruta_salida):
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
    encabezado = FIRMA + struct.pack("<BB", modo)
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
        partes = [struct.pack("<H", len(contenidos))]
        for ruta, datos in zip(rutas_entrada, contenidos):
            nombre   = ruta.name.encode("utf-8")
            r        = _comprimir_1d(datos)
            registro = _serializar_registro(r)
            partes.append(
                struct.pack("<B", len(nombre)) + nombre +
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
                           + struct.pack("<BB", MODO_ALMACENADO)
                           + struct.pack("<B", len(ext)) + ext
                           + contenidos[0])          # bytes originales, sin tocar
        stats["almacenado"] = True
    else:
        stats["almacenado"] = False

    Path(ruta_salida).write_bytes(contenido_final)
    stats["tam_comprimido"] = len(contenido_final)
    return stats


def descomprimir(ruta_mtxc, carpeta_salida=None):
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
        ruta      = Path(carpeta_salida) / (stem + "_reconstruido" + ext)
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

    return archivos_escritos