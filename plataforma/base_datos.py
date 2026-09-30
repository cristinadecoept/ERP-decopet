"""Capa de acceso PostgreSQL para conservar la interfaz de consultas de la app."""
import os
import re
import sqlite3
import contextvars
from urllib.parse import urlparse
from collections.abc import Mapping
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

ErrorBaseDatos = (SQLAlchemyError, sqlite3.Error)

from plataforma.modelo import metadata

_engine = None
_esquema_prueba = contextvars.ContextVar("decopet_esquema_prueba", default=None)
_conexiones_prueba = contextvars.ContextVar("decopet_conexiones_prueba", default=None)


def usa_postgres():
    return bool(os.environ.get("DECOPET_DATABASE_URL") or os.environ.get("DATABASE_URL"))


def nombre_base():
    return urlparse(_url().replace("postgresql+psycopg://", "postgresql://", 1)).path.lstrip("/")


def _sqlite(ruta=None):
    con = sqlite3.connect(ruta or Path(__file__).resolve().parent / "data" / "plataforma.db")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _motor():
    global _engine
    if _engine is None:
        _engine = create_engine(_url(), pool_pre_ping=True, pool_size=5, max_overflow=5)
    return _engine


class _SQLite:
    def __new__(cls, ruta=None):
        return _sqlite(ruta)


class Fila(dict):
    """Fila indexable por nombre o posición, igual que sqlite3.Row."""
    def __init__(self, mapping):
        super().__init__(mapping)
        self._claves = tuple(mapping.keys())

    def __getitem__(self, clave):
        if isinstance(clave, int):
            clave = self._claves[clave]
        return super().__getitem__(clave)


class Resultado:
    def __init__(self, resultado, lastrowid=None):
        self._resultado = resultado
        self.lastrowid = lastrowid
        self.rowcount = resultado.rowcount

    def fetchone(self):
        fila = self._resultado.fetchone()
        return Fila(fila._mapping) if fila is not None else None

    def fetchall(self):
        return [Fila(f._mapping) for f in self._resultado.fetchall()]

    def fetchmany(self, cantidad=None):
        filas = self._resultado.fetchmany(cantidad) if cantidad is not None else self._resultado.fetchmany()
        return [Fila(f._mapping) for f in filas]

    def __iter__(self):
        for fila in self._resultado:
            yield Fila(fila._mapping)


class Conexion:
    def __init__(self, conexion):
        self._conexion = conexion
        self._cerrada = False

    def execute(self, sql, parametros=()):
        pragma = re.fullmatch(r"\s*PRAGMA\s+table_info\((\w+)\)\s*;?", sql, re.I)
        if pragma:
            return _pragma_table_info(self._conexion, pragma.group(1))
        sql = traducir_conflictos(sql)
        sql, parametros = traducir(sql, parametros)
        sql, retorno = preparar_insert(sql)
        sql, parametros = _psycopg(sql, parametros)
        resultado = self._conexion.exec_driver_sql(sql, parametros)
        lastrowid = None
        if retorno:
            fila = resultado.fetchone()
            if fila is not None:
                lastrowid = fila[0]
        return Resultado(resultado, lastrowid)

    def executemany(self, sql, parametros):
        lote = list(parametros)
        if not lote:
            return Resultado(self._conexion.execute(text("SELECT 1"), {}))
        sql = traducir_conflictos(_sqlite_compat(sql))
        sql_nuevo, primera = _qmarks(sql, lote[0])
        valores = [primera] + [_qmarks(sql, p)[1] for p in lote[1:]]
        sql_nuevo, _ = _psycopg(sql_nuevo, valores[0])
        return Resultado(self._conexion.exec_driver_sql(sql_nuevo, valores))

    def executescript(self, sql):
        for sentencia in sql.split(";"):
            sentencia = sentencia.strip()
            if sentencia:
                self.execute(sentencia)
        return Resultado(self._conexion.execute(text("SELECT 1")))

    @property
    def row_factory(self):
        return Fila

    @row_factory.setter
    def row_factory(self, valor):
        pass

    def commit(self):
        self._conexion.commit()

    def rollback(self):
        self._conexion.rollback()

    def close(self):
        if not self._cerrada:
            self._conexion.close()
            self._cerrada = True

    def __enter__(self):
        return self

    def __exit__(self, tipo, valor, traza):
        if tipo:
            self.rollback()
        else:
            self.commit()
        self.close()


def _url():
    url = os.environ.get("DECOPET_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Falta DECOPET_DATABASE_URL o DATABASE_URL.")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def conectar():
    if not usa_postgres():
        return _sqlite()
    esquema = _esquema_prueba.get()
    motor = _motor()
    conexion = motor.connect()
    if esquema:
        conexion = conexion.execution_options(schema_translate_map={None: esquema})
        conexion.exec_driver_sql(f'SET search_path TO "{esquema}"')
    envoltura = Conexion(conexion)
    conexiones = _conexiones_prueba.get()
    if conexiones is not None:
        conexiones.append(envoltura)
    return envoltura


class EsquemaPrueba:
    def __init__(self, nombre):
        self.nombre = nombre or f"prueba_{uuid4().hex}"
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", self.nombre):
            raise ValueError("Nombre de esquema de prueba inválido")
        self._token = None
        self._token_conexiones = None
        self._conexiones = []

    def __enter__(self):
        if not usa_postgres():
            return self
        motor = _motor()
        with motor.begin() as conexion:
            conexion.exec_driver_sql(f'CREATE SCHEMA "{self.nombre}"')
        self._token = _esquema_prueba.set(self.nombre)
        self._token_conexiones = _conexiones_prueba.set(self._conexiones)
        metadata.create_all(motor.execution_options(schema_translate_map={None: self.nombre}))
        return self

    def __exit__(self, tipo, valor, traza):
        if self._token is not None:
            try:
                for conexion in self._conexiones:
                    conexion.close()
            finally:
                _conexiones_prueba.reset(self._token_conexiones)
                _esquema_prueba.reset(self._token)
                with _motor().begin() as conexion:
                    conexion.exec_driver_sql(f'DROP SCHEMA "{self.nombre}" CASCADE')
        return False


def esquema_prueba(nombre=None):
    return EsquemaPrueba(nombre)


def traducir_conflictos(sql):
    """Traduce la sintaxis de conflictos SQLite usando las restricciones del MetaData."""
    m = re.match(r"\s*INSERT\s+OR\s+(IGNORE|REPLACE)\s+INTO\s+([\w\"]+)", sql, re.I)
    if not m:
        return sql
    modo, tabla_raw = m.group(1).upper(), m.group(2)
    tabla = tabla_raw.strip('"').lower()
    definicion = metadata.tables.get(tabla)
    if definicion is None:
        raise ValueError(f"No hay esquema para INSERT OR {modo} en {tabla}")
    resto = sql[m.end():]
    if modo == "IGNORE":
        if re.search(r"\bON\s+CONFLICT\b", resto, re.I):
            return re.sub(r"^\s*INSERT\s+OR\s+IGNORE", "INSERT", sql, count=1, flags=re.I)
        return re.sub(r"^\s*INSERT\s+OR\s+IGNORE", "INSERT", sql, count=1, flags=re.I) + " ON CONFLICT DO NOTHING"
    candidatos = []
    conflictos = {
        "config": ("clave", ("valor",)),
        "tasas": (("fecha_valor", "fuente"), ("valor", "manual", "usuario_id", "nota")),
        "seguimientos": ("clave", ("cliente_id", "tipo", "resultado", "nota", "posponer_hasta", "usuario_id", "intentos")),
    }
    if tabla not in conflictos:
        raise ValueError(f"Falta declarar la clave y columnas para INSERT OR REPLACE en {tabla}")
    clave, actualizar = conflictos[tabla]
    claves = (clave,) if isinstance(clave, str) else clave
    columnas = ", ".join(f'"{c}"' for c in claves)
    sets = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in actualizar)
    prefix = re.sub(r"^\s*INSERT\s+OR\s+REPLACE", "INSERT", sql, count=1, flags=re.I)
    return prefix + f" ON CONFLICT ({columnas}) DO UPDATE SET {sets}"


def _pragma_table_info(conexion, tabla):
    t = metadata.tables.get(tabla.lower())
    if t is None:
        raise ValueError(f"Tabla desconocida en PRAGMA table_info: {tabla}")
    filas = []
    for i, col in enumerate(t.columns):
        filas.append(Fila({"cid": i, "name": col.name, "type": str(col.type).upper(),
                           "notnull": int(not col.nullable), "dflt_value": str(col.server_default.arg) if col.server_default is not None else None,
                           "pk": int(col.primary_key)}))
    class _PRAGMA(Resultado):
        def __init__(self, filas): self._filas = filas; self.lastrowid = None; self.rowcount = len(filas)
        def fetchone(self): return self._filas.pop(0) if self._filas else None
        def fetchall(self): filas, self._filas = self._filas, []; return filas
        def __iter__(self): return iter(self.fetchall())
    return _PRAGMA(filas)


def preparar_insert(sql):
    """Agrega RETURNING id a INSERTs de tablas con PK id."""
    if not re.match(r"\s*INSERT\s+INTO\s+", sql, re.I) or re.search(r"\bRETURNING\b", sql, re.I):
        return sql, False
    m = re.match(r"\s*INSERT\s+(?:OR\s+(?:IGNORE|REPLACE)\s+)?INTO\s+([\w\"]+)", sql, re.I)
    if not m:
        return sql, False
    tabla = m.group(1).strip('"').lower()
    definicion = metadata.tables.get(tabla)
    if definicion is None or len(definicion.primary_key.columns) != 1:
        return sql, False
    columna = next(iter(definicion.primary_key.columns)).name
    if columna != "id":
        return sql, False
    return sql.rstrip().rstrip(";") + " RETURNING id", True


def _qmarks(sql, parametros):
    """Convierte ? y los qmarks numerados de SQLite a :p0, :p1... fuera de literales."""
    if isinstance(parametros, Mapping):
        return sql, dict(parametros)
    valores = tuple(parametros or ())
    nombres = {}
    salida = []
    i = 0
    j = 0
    indice_sqlite = 0
    estado = "normal"
    while i < len(sql):
        c = sql[i]
        sig = sql[i:i + 2]
        if estado == "normal":
            if sig == "--":
                estado = "linea"
                salida.append(sig); i += 2; continue
            if sig == "/*":
                estado = "bloque"
                salida.append(sig); i += 2; continue
            if c == "'": estado = "simple"
            elif c == '"': estado = "doble"
            elif c == "$":
                m = re.match(r"\$[A-Za-z_0-9]*\$", sql[i:])
                if m:
                    estado = "dollar"; dollar = m.group(0)
                    salida.append(dollar); i += len(dollar); continue
            elif c == "?":
                m = re.match(r"\?(\d+)", sql[i:])
                if m:
                    indice_sqlite = int(m.group(1))
                    nombre = f"p{indice_sqlite - 1}"
                    nombres[nombre] = valores[indice_sqlite - 1]
                    salida.append(":" + nombre); i += len(m.group(0)); continue
                indice_sqlite += 1
                nombre = f"p{indice_sqlite - 1}"
                nombres[nombre] = valores[indice_sqlite - 1]
                salida.append(":" + nombre); i += 1; j += 1; continue
            salida.append(c); i += 1; continue
        if estado == "linea":
            salida.append(c); i += 1
            if c == "\n": estado = "normal"
            continue
        if estado == "bloque":
            salida.append(c); i += 1
            if sig == "*/": salida.append("/"); i += 1; estado = "normal"
            continue
        if estado == "simple":
            salida.append(c); i += 1
            if c == "'":
                if i < len(sql) and sql[i] == "'": salida.append("'"); i += 1
                else: estado = "normal"
            continue
        if estado == "doble":
            salida.append(c); i += 1
            if c == '"':
                if i < len(sql) and sql[i] == '"': salida.append('"'); i += 1
                else: estado = "normal"
            continue
        if estado == "dollar":
            if sql.startswith(dollar, i):
                salida.append(dollar); i += len(dollar); estado = "normal"
            else: salida.append(c); i += 1
    return "".join(salida), nombres


def _extraer_argumentos(sql, inicio):
    profundidad = 1
    comilla = None
    i = inicio
    while i < len(sql):
        c = sql[i]
        if comilla:
            if c == comilla:
                if i + 1 < len(sql) and sql[i + 1] == comilla:
                    i += 2; continue
                comilla = None
        elif c in "'\"": comilla = c
        elif c == "(": profundidad += 1
        elif c == ")":
            profundidad -= 1
            if profundidad == 0: return sql[inicio:i], i
        i += 1
    raise ValueError("Paréntesis sin cerrar en SQL")


def _group_concat(sql):
    patron = re.compile(r"\bGROUP_CONCAT\s*\(", re.I)
    while m := patron.search(sql):
        contenido, fin = _extraer_argumentos(sql, m.end())
        partes, actual, profundidad, comilla = [], [], 0, None
        for c in contenido:
            if comilla:
                actual.append(c)
                if c == comilla: comilla = None
            elif c in "'\"": comilla = c; actual.append(c)
            elif c == "(": profundidad += 1; actual.append(c)
            elif c == ")": profundidad -= 1; actual.append(c)
            elif c == "," and profundidad == 0:
                partes.append("".join(actual).strip()); actual = []
            else: actual.append(c)
        partes.append("".join(actual).strip())
        if len(partes) == 1:
            expr, sep = partes[0], "','"
            distinct = re.match(r"DISTINCT\s+", expr, re.I)
            prefix = "DISTINCT " if distinct else ""
            if distinct: expr = expr[distinct.end():]
        else:
            expr, sep = partes[0], partes[1]
            prefix = ""
            distinct = re.match(r"DISTINCT\s+", expr, re.I)
            if distinct: prefix = "DISTINCT "; expr = expr[distinct.end():]
        sql = sql[:m.start()] + f"string_agg({prefix}{expr}, {sep})" + sql[fin + 1:]
    return sql


def _fechas(sql):
    """Traduce funciones de fecha SQLite conservando el formato ISO TEXT de la app."""
    # datetime('now','localtime', '-N minutes/hours/days' o ?)
    patron = re.compile(r"datetime\(\s*'now'\s*(?:,\s*'localtime')?\s*((?:,\s*(?:'[^']*'|\?\d*))*)\s*\)", re.I)
    def dt(m):
        extras = re.findall(r"'([^']+)'|(\?\d*)", m.group(1))
        base = "LOCALTIMESTAMP"
        for literal, qmark in extras:
            if qmark:
                base = f"({base} + CAST({qmark} AS interval))"
                continue
            x = literal
            rel = re.fullmatch(r"([+-])\s*(\d+)\s+(minute|minutes|hour|hours|day|days|month|months|year|years)", x.strip(), re.I)
            if rel:
                operador = "+" if rel.group(1) == "+" else "-"
                base = f"({base} {operador} INTERVAL '{rel.group(2)} {rel.group(3)}')"
            elif x.lower() == "start of month":
                base = f"date_trunc('month', {base})"
        return f"to_char({base}, 'YYYY-MM-DD HH24:MI:SS')"
    sql = patron.sub(dt, sql)
    # date('now', '-N days') -> texto YYYY-MM-DD
    patron_date = re.compile(r"date\(\s*'now'\s*((?:,\s*'[^']*')*)\s*\)", re.I)
    def fecha(m):
        base = "CURRENT_DATE"
        for x in re.findall(r"'([^']+)'", m.group(1)):
            rel = re.fullmatch(r"([+-])\s*(\d+)\s+(day|days|month|months|year|years)", x.strip(), re.I)
            if rel:
                op = "+" if rel.group(1) == "+" else "-"
                base = f"({base} {op} INTERVAL '{rel.group(2)} {rel.group(3)}')::date"
            elif x.lower() == "start of month":
                base = f"date_trunc('month', {base})::date"
        return f"to_char({base}, 'YYYY-MM-DD')"
    return patron_date.sub(fecha, sql)


def _sqlite_compat(sql):
    sql = re.sub(r"\bPRAGMA\s+foreign_keys\s*=\s*ON\s*;?", "SELECT 1", sql, flags=re.I)
    sql = _group_concat(sql)
    sql = _fechas(sql)
    # SQLite's truthy integer flags are accepted as booleans by PostgreSQL only when explicit.
    sql = re.sub(r"\bjulianday\(\s*'now'\s*\)", "EXTRACT(EPOCH FROM CURRENT_TIMESTAMP) / 86400", sql, flags=re.I)
    sql = re.sub(r"\bjulianday\(\s*([^()]+?)\s*\)", r"(EXTRACT(EPOCH FROM CAST(\1 AS timestamp)) / 86400)", sql, flags=re.I)
    return sql


def traducir(sql, parametros=()):
    sql = _sqlite_compat(sql)
    sql, parametros = _qmarks(sql, parametros)
    return sql, parametros


def _psycopg(sql, parametros):
    nombres = []
    def proteger(m):
        nombre = m.group(1)
        token = f"__DECOPET_PARAM_{len(nombres)}__"
        nombres.append((token, nombre))
        return token
    sql = re.sub(r"(?<![\w:]):([A-Za-z_]\w*)", proteger, sql)
    sql = sql.replace("%", "%%")
    for token, nombre in nombres:
        sql = sql.replace(token, f"%({nombre})s")
    return sql, parametros


def crear_esquema():
    """Inicializa el esquema actual; producción debe usar Alembic en el deploy."""
    metadata.create_all(_motor())


def conectar_sqlite(ruta=None):
    """Compatibilidad temporal para llamadas existentes: PostgreSQL no usa rutas de archivo."""
    return conectar()
