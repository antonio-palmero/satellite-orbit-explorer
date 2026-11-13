# %% [markdown]
# # Satellite Orbit Explorer · built during Space Career Week 2026 · GNSS Academy
#
# 1. Configuración (setup)
# 2. Cargar el GP JSON
# 3. Convertir los elementos orbitales
# 4. Ventana de tiempo
# 5. Propagar
# 6. Resumen de las órbitas
# 7. Gráficas
# 8. KML e informe
#
# *Modelo: órbita kepleriana de dos cuerpos, Tierra esférica y rotación simple de
# Greenwich. Los registros GP contienen elementos medios de SGP4, así que esto es
# una aproximación educativa, no una predicción orbital de precisión.*

# %%
# --- Etapa 1: configuración ---
# Convenios: todas las fechas son UTC (con zona horaria), distancias en km y velocidades en km/s.
import html, json, math, time, zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.colors import to_hex, to_rgba
import requests

# Constantes físicas
MU_EARTH_KM3_S2 = 398600.4418  # parámetro gravitacional de la Tierra (km^3/s^2)
EARTH_RADIUS_KM = 6378.137     # radio ecuatorial, aquí usado como esfera (km)

# Carpeta del proyecto: /content en Colab; si no, la carpeta donde está este script
if Path('/content').is_dir():
    PROJECT_DIR = Path('/content')
elif '__file__' in globals():
    PROJECT_DIR = Path(__file__).resolve().parent
else:
    PROJECT_DIR = Path.cwd()  # "Run Cell" de VS Code arranca en la carpeta del script

# Carpetas: IN_DIR guarda el GP JSON de entrada; OUT_DIR recibe gráficas, KML e informe
BASE_DIR = PROJECT_DIR / 'satellite_orbits'
IN_DIR = BASE_DIR / 'input'
OUT_DIR = BASE_DIR / 'output'
for folder in (IN_DIR, OUT_DIR):
    folder.mkdir(parents=True, exist_ok=True)  # se puede volver a ejecutar sin problema

# Configuración de la ejecución
STEP_SECONDS = 5      # tiempo entre puntos propagados (s)
DURATION_HOURS = 24   # duración de la ventana de tiempo (h)
LIVE_GROUP = None     # solo lo usará una ampliación opcional de descarga en directo

print('Satellite Orbit Explorer: configuration')
print(f'  PROJECT_DIR     = {PROJECT_DIR}')
print(f'  MU_EARTH_KM3_S2 = {MU_EARTH_KM3_S2} km^3/s^2')
print(f'  EARTH_RADIUS_KM = {EARTH_RADIUS_KM} km')
print(f'  IN_DIR          = {IN_DIR}  (exists: {IN_DIR.is_dir()})')
print(f'  OUT_DIR         = {OUT_DIR}  (exists: {OUT_DIR.is_dir()})')
print(f'  STEP_SECONDS    = {STEP_SECONDS} s')
print(f'  DURATION_HOURS  = {DURATION_HOURS} h')
print(f'  LIVE_GROUP      = {LIVE_GROUP}')

# %% [markdown]
# ## Etapa 2 · Cargar el GP JSON
#
# El OMM JSON da a cada elemento orbital un campo con nombre (como `INCLINATION`)
# y escribe el epoch como una fecha ISO normal, mientras que el antiguo formato TLE
# mete los números en columnas fijas de caracteres, con el año en dos cifras y el
# día del año. Además, el JSON admite números de catálogo NORAD de seis cifras, que
# ya no caben en el campo de cinco caracteres de un TLE.

# %%
# --- Etapa 2: obtener los archivos de entrada y cargar los datos GP ---
if 'IN_DIR' not in globals():
    raise NameError('IN_DIR is not defined: run the Stage 1 cell first.')

INPUT_FILES = ('orbital_elements.json', 'data_manifest.json', 'world_map.txt')
UPLOAD_DIR = PROJECT_DIR  # dónde va el zip: la carpeta del proyecto (o /content en Colab)
REQUIRED_GP_FIELDS = ('OBJECT_NAME', 'NORAD_CAT_ID', 'EPOCH', 'MEAN_MOTION',
                      'ECCENTRICITY', 'INCLINATION', 'RA_OF_ASC_NODE',
                      'ARG_OF_PERICENTER', 'MEAN_ANOMALY')


def extract_inputs(timeout_s=15 * 60, poll_s=2):
    """Pone los tres archivos de entrada en IN_DIR, esperando a que se suba el zip si hace falta."""
    if all((IN_DIR / name).is_file() for name in INPUT_FILES):
        print(f'Input files already in {IN_DIR}, nothing to do.')
        return

    print(f'Put orbit_workshop_inputs.zip in {UPLOAD_DIR} (in Colab: drag it into the Files panel). Waiting...')
    deadline = time.time() + timeout_s
    while True:
        # Un zip que aún se está subiendo no pasa is_zipfile: si pasa, la subida ha terminado
        zips = [p for p in UPLOAD_DIR.glob('orbit_workshop_inputs*.zip') if zipfile.is_zipfile(p)]
        if zips:
            break
        if time.time() > deadline:
            raise TimeoutError(f'No complete orbit_workshop_inputs*.zip found in {UPLOAD_DIR} after 15 minutes.')
        time.sleep(poll_s)

    zip_path = max(zips, key=lambda p: p.stat().st_mtime)  # el más reciente si hay varios
    with zipfile.ZipFile(zip_path) as zf:
        # Se busca solo por nombre de archivo, así pueden estar dentro de una carpeta del zip
        members = {Path(m).name: m for m in zf.namelist() if not m.endswith('/')}
        missing = [name for name in INPUT_FILES if name not in members]
        if missing:
            raise FileNotFoundError(f'{zip_path.name} does not contain: {missing}')
        for name in INPUT_FILES:  # extrae solo estos tres archivos
            (IN_DIR / name).write_bytes(zf.read(members[name]))
    print(f'Extracted {len(INPUT_FILES)} files from {zip_path.name} into {IN_DIR}')


def read_gp_json(path):
    """Carga una lista GP JSON de CelesTrak y comprueba que cada registro tiene los campos necesarios."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f'{path} not found: run extract_inputs() first.')
    records = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(records, list) or not records:
        raise ValueError(f'{path.name} must contain a non-empty JSON list of GP records.')
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            raise ValueError(f'Record {i} in {path.name} is not a JSON object.')
        missing = [field for field in REQUIRED_GP_FIELDS if field not in rec]
        if missing:
            raise ValueError(f"Record {i} ({rec.get('OBJECT_NAME', '?')}) is missing: {missing}")
    return records


# Ejecutarlas
extract_inputs()
sats = read_gp_json(IN_DIR / 'orbital_elements.json')
data_manifest = json.loads((IN_DIR / 'data_manifest.json').read_text(encoding='utf-8'))
if 'retrieved_utc' not in data_manifest:
    raise KeyError("data_manifest.json has no 'retrieved_utc' field.")

source_note = (f'CelesTrak GP data (OMM JSON), {len(sats)} satellites, '
               f"retrieved (UTC) {data_manifest['retrieved_utc']}")
print(source_note)
for s in sats:
    print(f"  {s['OBJECT_NAME']:<25} NORAD {s['NORAD_CAT_ID']:>6}   EPOCH {s['EPOCH']}")

# %%
# --- Ampliación opcional: datos en directo de CelesTrak ---
# Con LIVE_GROUP = None (Etapa 1) se usa la instantánea del taller. Con un grupo
# (por ejemplo 'STARLINK') se descargan sus datos GP actuales y se usan 4 satélites.
if 'sats' not in globals():
    raise NameError('sats is not defined: run the Stage 2 cell first.')

LIVE_CACHE_HOURS = 2  # CelesTrak pide no consultar más a menudo: reutilizamos la caché

if LIVE_GROUP is None:
    print('LIVE_GROUP is None: using the workshop snapshot.')
else:
    cache_path = IN_DIR / f'gp_{LIVE_GROUP}.json'
    now = datetime.now(timezone.utc)
    cache_age_h = ((now.timestamp() - cache_path.stat().st_mtime) / 3600
                   if cache_path.is_file() else None)

    if cache_age_h is None or cache_age_h > LIVE_CACHE_HOURS:
        # Usar los certificados del sistema operativo (necesario si un antivirus como Avast
        # inspecciona HTTPS). Si truststore no está instalado (p. ej. en Colab), no hace nada.
        try:
            import truststore
            truststore.inject_into_ssl()
        except ImportError:
            pass

        # Descargar solo si no hay caché o tiene más de 2 horas
        url = f'https://celestrak.org/NORAD/elements/gp.php?GROUP={LIVE_GROUP}&FORMAT=JSON'
        print(f'Downloading {url}')
        response = requests.get(url, timeout=30)
        if not response.ok:                  # CelesTrak explica el motivo en el texto (p. ej. 403 por consultar demasiado)
            print(f'CelesTrak answered {response.status_code}: {response.text.strip()[:300]}')
        response.raise_for_status()          # error claro si la respuesta HTTP es un fallo
        data = response.json()               # error claro si no es JSON (p. ej. grupo inexistente)
        if not isinstance(data, list) or not data:
            raise ValueError(f'CelesTrak returned no GP records for group {LIVE_GROUP!r}.')
        cache_path.write_text(response.text, encoding='utf-8')  # solo se guarda si todo fue bien
        retrieved = now
    else:
        print(f'Using cache {cache_path.name} ({cache_age_h:.1f} h old, limit {LIVE_CACHE_HOURS} h)')
        retrieved = datetime.fromtimestamp(cache_path.stat().st_mtime, timezone.utc)

    # Reutilizamos read_gp_json de la Etapa 2: comprueba los campos de cada registro
    live = read_gp_json(cache_path)
    sats = sorted(live, key=lambda rec: int(rec['NORAD_CAT_ID']))[:4]  # los 4 primeros por NORAD
    source_note = (f'CelesTrak GP data (OMM JSON), group {LIVE_GROUP}, {len(sats)} of {len(live)} satellites, '
                   f'retrieved (UTC) {retrieved:%Y-%m-%dT%H:%M:%SZ}')
    print(source_note)
    for s in sats:
        print(f"  {s['OBJECT_NAME']:<25} NORAD {s['NORAD_CAT_ID']:>6}   EPOCH {s['EPOCH']}")

# %% [markdown]
# ## Etapa 3 · Convertir los elementos orbitales
#
# | Campo GP            | Símbolo | Conversión a unidades del modelo                 |
# |---------------------|---------|--------------------------------------------------|
# | `EPOCH`             | t0      | texto ISO -> fecha UTC                           |
# | `MEAN_MOTION`       | n       | rev/día x 2*pi / 86400 -> rad/s                  |
# | (a partir de n)     | a       | a = (mu / n^2)^(1/3) -> km (3ª ley de Kepler)    |
# | `ECCENTRICITY`      | e       | ya es decimal, sin conversión                    |
# | `INCLINATION`       | i       | grados -> radianes                               |
# | `RA_OF_ASC_NODE`    | RAAN    | grados -> radianes                               |
# | `ARG_OF_PERICENTER` | omega   | grados -> radianes                               |
# | `MEAN_ANOMALY`      | M0      | grados -> radianes                               |
#
# *Nota: los valores GP son elementos medios de SGP4 (promediados para el modelo
# SGP4), no elementos osculadores reales, así que usarlos en un modelo puro de dos
# cuerpos es una aproximación.*

# %%
# --- Etapa 3: convertir cada registro GP en elementos de Kepler en unidades del modelo ---
if 'sats' not in globals():
    raise NameError('sats is not defined: run the Stage 2 cell first.')


def parse_gp_epoch(text):
    """Los epochs de CelesTrak están en UTC sin desfase: se leen y se marcan como UTC."""
    return datetime.fromisoformat(text.rstrip('Z')).replace(tzinfo=timezone.utc)


def parse_gp_elements(rec):
    """Un registro GP -> diccionario de elementos de Kepler (km, rad, rad/s, fecha UTC)."""
    name = rec['OBJECT_NAME'].strip()
    norad_id = int(rec['NORAD_CAT_ID'])
    n = 2 * math.pi * float(rec['MEAN_MOTION']) / 86400   # rev/día -> rad/s
    return {
        'name': name,
        'norad_id': norad_id,
        'label': f'{name} [{norad_id}]',
        'epoch': parse_gp_epoch(rec['EPOCH']),
        'n_rad_s': n,
        'a_km': (MU_EARTH_KM3_S2 / n**2) ** (1 / 3),       # tercera ley de Kepler
        'e': float(rec['ECCENTRICITY']),                   # ya es decimal
        'i_rad': math.radians(float(rec['INCLINATION'])),
        'raan_rad': math.radians(float(rec['RA_OF_ASC_NODE'])),
        'argp_rad': math.radians(float(rec['ARG_OF_PERICENTER'])),
        'M0_rad': math.radians(float(rec['MEAN_ANOMALY'])),
    }


def elements_table(elements):
    """Líneas de una tabla de texto con los elementos (ángulos en grados)."""
    lines = [f"{'Name':<25} {'NORAD':>6}  {'Epoch (UTC)':<19} {'a [km]':>10} {'e':>9} "
             f"{'i [deg]':>8} {'RAAN':>8} {'argp':>8} {'M0':>8}"]
    lines.append('-' * len(lines[0]))
    for el in elements:
        lines.append(f"{el['name']:<25} {el['norad_id']:>6}  "
                     f"{el['epoch']:%Y-%m-%d %H:%M:%S} {el['a_km']:>10.1f} {el['e']:>9.7f} "
                     f"{math.degrees(el['i_rad']):>8.3f} {math.degrees(el['raan_rad']):>8.3f} "
                     f"{math.degrees(el['argp_rad']):>8.3f} {math.degrees(el['M0_rad']):>8.3f}")
    return lines


elements = [parse_gp_elements(s) for s in sats]
print('\n'.join(elements_table(elements)))

# %% [markdown]
# ## Etapa 4 · Ventana de tiempo
#
# Todos los satélites comparten una misma malla de tiempo UTC, pero cada registro
# se propaga desde su propio epoch. Cuanto más lejos está un instante de ese epoch,
# mayor es el error de nuestro modelo simple de dos cuerpos, así que importa el
# desfase entre el inicio de la ventana y cada epoch. El registro de QZS-2 del
# taller es del 17 de octubre de 2026, más de tres semanas antes de la ventana, así
# que sus posiciones son las menos fiables.

# %%
# --- Etapa 4: una malla de tiempo UTC común para todos los satélites ---
if 'elements' not in globals():
    raise NameError('elements is not defined: run the Stage 3 cell first.')

STALE_DAYS = 7  # los desfases mayores que esto se marcan como registro antiguo

# Empieza a medianoche UTC del epoch más reciente; la ventana dura DURATION_HOURS
newest_epoch = max(el['epoch'] for el in elements)
start = newest_epoch.replace(hour=0, minute=0, second=0, microsecond=0)
end = start + timedelta(hours=DURATION_HOURS)

# Malla cada STEP_SECONDS, incluyendo los dos extremos
n_points = int((end - start).total_seconds()) // STEP_SECONDS + 1
times = [start + timedelta(seconds=k * STEP_SECONDS) for k in range(n_points)]

print(f'Window (UTC): {start:%Y-%m-%d %H:%M:%S} -> {end:%Y-%m-%d %H:%M:%S}')
print(f'Step: {STEP_SECONDS} s   n_points: {n_points}')
print('Offset start - epoch per satellite:')
for el in elements:
    offset_days = (start - el['epoch']).total_seconds() / 86400
    flag = '  <-- STALE record (> 7 days)' if offset_days > STALE_DAYS else ''
    print(f"  {el['label']:<35} {offset_days:+8.2f} days{flag}")

# %% [markdown]
# ## Etapa 5 · Propagar
#
# Para cada instante de la malla movemos cada satélite a lo largo de su elipse de
# Kepler fija (modelo de dos cuerpos) y después giramos la Tierra debajo de él
# para obtener latitud, longitud y altura sobre una Tierra esférica. Los siete
# pasos numerados de `propagate` siguen la física en orden: en qué punto de la
# órbita está (anomalías), a qué distancia de la Tierra (radio), dónde está en el
# espacio (x, y, z inerciales), sobre qué punto de la Tierra en rotación
# (rotación de Greenwich) y a qué velocidad va (vis-viva).

# %%
# --- Etapa 5: propagar las órbitas ---
if 'times' not in globals():
    raise NameError('times is not defined: run the Stage 4 cell first.')

J2000 = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)  # epoch de referencia del GMST


def solve_kepler(M, e, tol=1e-12, max_iter=50):
    """Resuelve la ecuación de Kepler M = E - e*sin(E) para la anomalía excéntrica E (Newton-Raphson)."""
    E = math.pi if e > 0.8 else M          # buen valor inicial
    for _ in range(max_iter):
        f = E - e * math.sin(E) - M         # cuánto nos falta para la solución
        dE = f / (1 - e * math.cos(E))      # paso de Newton: f / f'
        E -= dE
        if abs(dE) < tol:
            return E
    raise RuntimeError(f'Kepler solver did not converge (M={M}, e={e}).')


def gmst_rad(t):
    """Tiempo sidéreo medio de Greenwich (IAU-1982, Meeus ec. 12.4): ángulo de rotación de la Tierra en rad."""
    d = (t - J2000).total_seconds() / 86400      # días desde J2000
    T = d / 36525                                # siglos julianos desde J2000
    theta = (280.46061837 + 360.98564736629 * d
             + 0.000387933 * T**2 - T**3 / 38710000) % 360
    return math.radians(theta)


def propagate(el, times):
    """Propagación de dos cuerpos de un satélite: un diccionario de punto por cada instante."""
    a, e, i = el['a_km'], el['e'], el['i_rad']
    raan, argp = el['raan_rad'], el['argp_rad']
    points = []
    for t in times:
        # 1) Anomalía media: crece de forma uniforme con el tiempo desde el epoch del registro
        dt = (t - el['epoch']).total_seconds()
        M = (el['M0_rad'] + el['n_rad_s'] * dt) % (2 * math.pi)

        # 2) Anomalía excéntrica: resolver la ecuación de Kepler
        E = solve_kepler(M, e)

        # 3) Radio, anomalía verdadera (ángulo real desde el perigeo) y argumento de latitud
        r = a * (1 - e * math.cos(E))
        nu = math.atan2(math.sqrt(1 - e**2) * math.sin(E), math.cos(E) - e)
        u = argp + nu                                  # ángulo desde el nodo ascendente

        # 4) Posición inercial (ECI): girar el plano de la órbita según RAAN e inclinación
        x = r * (math.cos(raan) * math.cos(u) - math.sin(raan) * math.sin(u) * math.cos(i))
        y = r * (math.sin(raan) * math.cos(u) + math.cos(raan) * math.sin(u) * math.cos(i))
        z = r * math.sin(u) * math.sin(i)

        # 5) Ejes fijos a la Tierra: deshacer el ángulo de rotación theta alrededor del eje z
        theta = gmst_rad(t)
        xf = math.cos(theta) * x + math.sin(theta) * y
        yf = -math.sin(theta) * x + math.cos(theta) * y

        # 6) Tierra esférica: latitud, longitud y altura
        lat = math.degrees(math.atan2(z, math.hypot(xf, yf)))
        lon = math.degrees(math.atan2(yf, xf))
        alt = r - EARTH_RADIUS_KM

        # 7) Velocidad con la ecuación vis-viva
        speed = math.sqrt(MU_EARTH_KM3_S2 * (2 / r - 1 / a))

        points.append({'time': t, 'lat_deg': lat, 'lon_deg': lon,
                       'alt_km': alt, 'speed_km_s': speed})
    return points


# Comprobaciones
assert abs(math.degrees(gmst_rad(J2000)) - 280.46) < 0.01, 'GMST at J2000 should be ~280.46 deg'
for M_test in (0.1, 1.0, 3.0, 5.5):
    for e_test in (0.0, 0.1, 0.5, 0.9):
        E_test = solve_kepler(M_test, e_test)
        assert abs(E_test - e_test * math.sin(E_test) - M_test) < 1e-10, 'Kepler residual too large'
print('Checks passed: GMST at J2000 and Kepler solver residuals.')

tracks = [propagate(el, times) for el in elements]

for el, track in zip(elements, tracks):
    if el['norad_id'] == 38652:  # SES-5 es geoestacionario, a unos 5 grados Este
        lons = [p['lon_deg'] for p in track]
        assert all(4 <= lon <= 6 for lon in lons), \
            f'SES-5 longitude out of 4..6 E (min {min(lons):.2f}, max {max(lons):.2f}): check GMST/signs'
        print(f'Check passed: SES-5 longitude stays in {min(lons):.3f}..{max(lons):.3f} deg E.')

for el, track in zip(elements, tracks):
    print(el['label'])
    for p in (track[0], track[len(track) // 2], track[-1]):
        print(f"  {p['time']:%Y-%m-%d %H:%M:%S}  lat {p['lat_deg']:+8.3f}  lon {p['lon_deg']:+9.3f}  "
              f"alt {p['alt_km']:9.1f} km  speed {p['speed_km_s']:6.3f} km/s")

# %% [markdown]
# ## Etapa 6 · Resumen de las órbitas
#
# Cómo leer la tabla:
# - **PULSAR-0**: periodo de unas 1,58 h e i de unos 97,5°. Una inclinación mayor
#   de 90° indica una órbita retrógrada; esta órbita baja casi polar es
#   heliosíncrona (cruza cada latitud a la misma hora solar local).
# - **Galileo 29**: periodo de unas 14,08 h, la órbita media (MEO) de Galileo.
# - **SES-5 y QZS-2**: periodo de unas 23,93 h, un día sidéreo (una rotación real
#   de la Tierra), así que las dos repiten su traza en tierra cada día.
# - **QZS-2** tiene e de unos 0,07 e i de unos 39°: es una órbita geosíncrona
#   inclinada (IGSO), no geoestacionaria. No se queda quieta sobre un punto;
#   dibuja un ocho sobre Japón y Australia.

# %%
# --- Etapa 6: resumen de las órbitas ---
if 'tracks' not in globals():
    raise NameError('tracks is not defined: run the Stage 5 cell first.')


def orbit_summary(el, pts):
    """Valores clave de una órbita, a partir de sus elementos y sus puntos propagados."""
    a, e = el['a_km'], el['e']
    speeds = [p['speed_km_s'] for p in pts]
    return {
        'name': el['name'],
        'norad_id': el['norad_id'],
        'period_h': 2 * math.pi / el['n_rad_s'] / 3600,   # una vuelta, en horas
        'perigee_km': a * (1 - e) - EARTH_RADIUS_KM,      # altura mínima
        'apogee_km': a * (1 + e) - EARTH_RADIUS_KM,       # altura máxima
        'e': e,
        'i_deg': math.degrees(el['i_rad']),
        'v_min_km_s': min(speeds),
        'v_max_km_s': max(speeds),
    }


def summary_table(summaries):
    """Líneas de una tabla de texto con los resúmenes de las órbitas."""
    lines = [f"{'Name':<25} {'NORAD':>6} {'Period [h]':>10} {'Perigee [km]':>12} {'Apogee [km]':>12} "
             f"{'e':>9} {'i [deg]':>8} {'v min':>7} {'v max':>7}"]
    lines.append('-' * len(lines[0]))
    for s in summaries:
        lines.append(f"{s['name']:<25} {s['norad_id']:>6} {s['period_h']:>10.3f} {s['perigee_km']:>12.1f} "
                     f"{s['apogee_km']:>12.1f} {s['e']:>9.7f} {s['i_deg']:>8.3f} "
                     f"{s['v_min_km_s']:>7.3f} {s['v_max_km_s']:>7.3f}")
    lines.append('(speeds in km/s, min and max over the time window)')
    return lines


summaries = [orbit_summary(el, pts) for el, pts in zip(elements, tracks)]
print('\n'.join(summary_table(summaries)))

# Comprobación: el periodo de la órbita de Galileo es de unas 14,08 h
for s in summaries:
    if s['norad_id'] == 59598:
        assert 14.0 <= s['period_h'] <= 14.2, f"Galileo 29 period {s['period_h']:.3f} h is outside 14.0..14.2 h"
        print(f"Check passed: Galileo 29 period = {s['period_h']:.3f} h.")

# %% [markdown]
# ## Etapa 7 · Gráficas
#
# **Trazas en tierra** (el punto de la Tierra justo debajo de cada satélite):
# - **PULSAR-0**: muchas pasadas (unas 15 al día). Cada pasada cae más al oeste,
#   porque la Tierra gira hacia el este debajo durante cada órbita de 1,58 h.
# - **Galileo 29**: una onda ancha entre unos 55° N y 55° S (su inclinación), que
#   se desplaza poco a poco porque su periodo no es una fracción exacta del día.
# - **SES-5**: un único punto. Su periodo es igual a una rotación de la Tierra y
#   orbita sobre el ecuador, así que se queda encima del mismo punto.
# - **QZS-2**: un ocho asimétrico. Tiene el mismo periodo que SES-5, pero la
#   inclinación de 39° lo lleva al norte y al sur. Su apogeo está en el norte,
#   donde va lento y pasa más tiempo, así que el bucle norte sobre Japón es
#   pequeño y estrecho; cerca del perigeo, en el sur, va más rápido y barre un
#   bucle más ancho sobre Australia.
#
# **Velocidad**: en una órbita excéntrica, el satélite "cae" hacia la Tierra de
# camino al perigeo y cambia altura (energía potencial) por velocidad (energía
# cinética), así que va más rápido en el perigeo y más lento en el apogeo
# (vis-viva, segunda ley de Kepler). Las órbitas casi circulares mantienen una
# velocidad casi constante.

# %%
# --- Etapa 7: gráficas con Matplotlib ---
if 'tracks' not in globals():
    raise NameError('tracks is not defined: run the Stage 5 cell first.')

COLORS = ['tab:blue', 'tab:orange', 'tab:green', 'tab:red']  # uno por satélite (se reutiliza en la Etapa 8)
if len(elements) > len(COLORS):
    raise ValueError(f'{len(elements)} satellites but only {len(COLORS)} colours defined.')


def load_coastline(path):
    """Lee líneas 'latitud longitud'; las líneas en blanco separan segmentos. Devuelve listas de (lon, lat)."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f'{path} not found: run the Stage 2 cell first.')
    segments, current = [], []
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():                 # línea en blanco: cierra el segmento actual
            if current:
                segments.append(current)
            current = []
            continue
        lat, lon = map(float, line.split())  # en el archivo la latitud va PRIMERO
        current.append((lon, lat))
    if current:
        segments.append(current)
    return segments


coastline = load_coastline(IN_DIR / 'world_map.txt')
print(f'Coastline: {len(coastline)} segments, {sum(len(s) for s in coastline)} points')

# Figura 1: trazas en tierra
fig, ax = plt.subplots(figsize=(12, 6))
for seg in coastline:                        # cada segmento se dibuja por separado, nunca unidos
    lons, lats = zip(*seg)
    ax.plot(lons, lats, color='gray', linewidth=0.6)
for el, pts, color in zip(elements, tracks, COLORS):
    # puntos pequeños en vez de líneas: sin líneas falsas cuando una traza salta de +180 a -180 grados
    ax.scatter([p['lon_deg'] for p in pts], [p['lat_deg'] for p in pts],
               s=1, color=color, label=el['label'])
ax.set_xlim(-180, 180)
ax.set_ylim(-90, 90)
ax.set_xlabel('Longitude [deg]')
ax.set_ylabel('Latitude [deg]')
ax.set_title(f'Ground tracks, {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC')
ax.legend(loc='lower left', markerscale=8, fontsize=8)
ax.grid(alpha=0.3)
fig.savefig(OUT_DIR / 'ground_tracks.png', dpi=200, bbox_inches='tight')
print(f"Saved {OUT_DIR / 'ground_tracks.png'}")
plt.show()

# Figura 2: velocidad frente al tiempo, un subgráfico apilado por satélite
fig, axes = plt.subplots(len(elements), 1, figsize=(10, 2.2 * len(elements)), sharex=True, squeeze=False)
for ax, el, pts, color in zip(axes[:, 0], elements, tracks, COLORS):
    ax.plot([p['time'] for p in pts], [p['speed_km_s'] for p in pts], color=color)
    ax.set_ylabel('Speed [km/s]')
    ax.set_title(el['label'], fontsize=9)
    ax.ticklabel_format(axis='y', useOffset=False)   # valores reales, sin el desplazamiento "+7.6"
    ax.grid(alpha=0.3)
axes[-1, 0].set_xlabel('Time [UTC]')
fig.tight_layout()
fig.savefig(OUT_DIR / 'velocity_timeseries.png', dpi=200, bbox_inches='tight')
print(f"Saved {OUT_DIR / 'velocity_timeseries.png'}")
plt.show()

# %% [markdown]
# ## Etapa 8 · KML e informe
#
# Exportamos dos archivos KML para Google Earth (las órbitas en 3D fijas a la
# Tierra en rotación y una traza en tierra animada), un CZML para CesiumJS (los
# satélites moviéndose en 3D con su órbita), escribimos un informe de texto y
# comprimimos todos los resultados en un zip.
#
# **CZML** es JSON: un array de "paquetes". El primero (`id: "document"`) fija el
# reloj de la escena; cada satélite es otro paquete con una posición muestreada
# (`epoch` + `[t, lon, lat, alt_m, ...]`, t en segundos desde el epoch) que Cesium
# interpola entre muestras. Cesium convierte cada muestra a ECEF cartesiano antes
# de interpolar, así que aquí no hace falta partir las trazas en el salto de
# longitud +180/-180 como en el KML. Ojo: Cesium lee lat/alt como geodésicas
# sobre el elipsoide WGS84 y las nuestras son de una Tierra esférica, así que
# cerca de los polos la posición puede desviarse unos 20 km (igual que en el KML).

# %%
# --- Etapa 8: exportar para Google Earth y escribir el informe ---
if 'summaries' not in globals() or 'COLORS' not in globals():
    raise NameError('summaries or COLORS is not defined: run the Stage 6 and Stage 7 cells first.')

KML_NS = 'http://www.opengis.net/kml/2.2'
GX_NS = 'http://www.google.com/kml/ext/2.2'
ET.register_namespace('', KML_NS)    # etiquetas <kml> normales
ET.register_namespace('gx', GX_NS)   # etiquetas de la extensión de Google: <gx:Track>, ...
ICON_URL = 'http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png'  # icono blanco
ANIM_STEP_S = 60                     # un punto de animación cada 60 s


def k(tag):
    """Nombre de etiqueta KML con su espacio de nombres."""
    return f'{{{KML_NS}}}{tag}'


def sub(parent, tag, text=None):
    """Añade un elemento hijo (con texto opcional) y lo devuelve."""
    child = ET.SubElement(parent, tag)
    if text is not None:
        child.text = text
    return child


def kml_color(color):
    """Color de Matplotlib -> 'aabbggrr' de KML (opaco). '#1f77b4' -> 'ffb4771f'."""
    h = to_hex(color)[1:]                # 'rrggbb'
    return 'ff' + h[4:6] + h[2:4] + h[0:2]


def new_kml(title, line_width=2, icon_scale=1.0, label_scale=None):
    """Empieza un documento KML con un Style por satélite (mismos colores que las figuras).
    label_scale: si se indica, el nombre del satélite aparece junto a su icono con ese tamaño."""
    root = ET.Element(k('kml'))
    doc = sub(root, k('Document'))
    sub(doc, k('name'), title)
    for idx, color in enumerate(COLORS[:len(elements)]):
        style = sub(doc, k('Style'))
        style.set('id', f'sat{idx}')
        icon_style = sub(style, k('IconStyle'))
        sub(icon_style, k('color'), kml_color(color))
        sub(icon_style, k('scale'), str(icon_scale))
        sub(sub(icon_style, k('Icon')), k('href'), ICON_URL)
        if label_scale:                       # etiqueta con el nombre junto al icono en movimiento
            label_style = sub(style, k('LabelStyle'))
            sub(label_style, k('color'), kml_color(color))
            sub(label_style, k('scale'), str(label_scale))
        line_style = sub(style, k('LineStyle'))
        sub(line_style, k('color'), kml_color(color))
        sub(line_style, k('width'), str(line_width))
    return root, doc


def save_kml(root, path):
    """Guarda el KML con sangría para que sea legible."""
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True)
    print(f'Saved {path}')


# 1) Órbitas 3D en ejes fijos a la Tierra: un LineString por satélite, altura en metros
root, doc = new_kml('Multi-Satellite Orbits | Earth-fixed')
for idx, (el, pts) in enumerate(zip(elements, tracks)):
    pm = sub(doc, k('Placemark'))
    sub(pm, k('name'), el['label'])
    sub(pm, k('styleUrl'), f'#sat{idx}')
    line = sub(pm, k('LineString'))
    sub(line, k('altitudeMode'), 'absolute')
    sub(line, k('coordinates'), ' '.join(
        f"{p['lon_deg']:.5f},{p['lat_deg']:.5f},{p['alt_km'] * 1000:.0f}" for p in pts))
save_kml(root, OUT_DIR / 'orbit_tracks_ecef.kml')

# 2) Trazas en tierra animadas: un gx:MultiTrack por satélite, un punto cada 60 s
every = max(1, ANIM_STEP_S // STEP_SECONDS)
# Trazas más gruesas, iconos más grandes y etiquetas con el nombre para seguir bien la animación
root, doc = new_kml('Multi-Satellite Ground Tracks | Animation',
                    line_width=5, icon_scale=1.4, label_scale=1.2)
for idx, (el, pts) in enumerate(zip(elements, tracks)):
    pm = sub(doc, k('Placemark'))
    sub(pm, k('name'), el['label'])
    sub(pm, k('styleUrl'), f'#sat{idx}')
    multi = sub(pm, f'{{{GX_NS}}}MultiTrack')
    # Partir en trozos allí donde la longitud salta de +180 a -180 grados
    pieces, piece = [], []
    for p in pts[::every]:
        if piece and abs(p['lon_deg'] - piece[-1]['lon_deg']) > 180:
            pieces.append(piece)
            piece = []
        piece.append(p)
    pieces.append(piece)
    for piece in pieces:
        trk = sub(multi, f'{{{GX_NS}}}Track')
        sub(trk, k('altitudeMode'), 'clampToGround')
        for p in piece:                       # primero todas las marcas de tiempo...
            sub(trk, k('when'), f"{p['time']:%Y-%m-%dT%H:%M:%SZ}")
        for p in piece:                       # ...y después las coordenadas correspondientes
            sub(trk, f'{{{GX_NS}}}coord', f"{p['lon_deg']:.5f} {p['lat_deg']:.5f} 0")
save_kml(root, OUT_DIR / 'orbit_animation.kml')

# 3) CZML para CesiumJS
CZML_STEP_S = 60   # una muestra de posición cada 60 s; Cesium interpola entre medias


def czml_time(t):
    """Fecha UTC -> texto ISO 8601 que entiende CZML."""
    return f'{t:%Y-%m-%dT%H:%M:%SZ}'


def czml_rgba(color):
    """Color de Matplotlib -> [r, g, b, a] en 0..255. 'tab:blue' -> [31, 119, 180, 255]."""
    return [round(c * 255) for c in to_rgba(color)]


def czml_description(s):
    """Tabla HTML con los datos de la órbita: Cesium la muestra en el InfoBox al hacer clic."""
    rows = [('NORAD ID', f"{s['norad_id']}"),
            ('Period', f"{s['period_h']:.3f} h"),
            ('Perigee altitude', f"{s['perigee_km']:.1f} km"),
            ('Apogee altitude', f"{s['apogee_km']:.1f} km"),
            ('Eccentricity (e)', f"{s['e']:.7f}"),
            ('Inclination (i)', f"{s['i_deg']:.3f}&deg;")]
    body = ''.join(f'<tr><th style="text-align:left">{k}</th><td>{v}</td></tr>' for k, v in rows)
    return (f"<h3>{html.escape(s['name'])}</h3><table>{body}</table>"
            '<p><i>Two-body Kepler model from SGP4 mean elements (educational approximation).</i></p>')


def export_czml(path, step_s=CZML_STEP_S):
    """Escribe un CZML con el reloj de la ventana y un paquete por satélite (mismos colores que las figuras)."""
    if step_s % STEP_SECONDS:
        raise ValueError(f'step_s ({step_s} s) must be a multiple of STEP_SECONDS ({STEP_SECONDS} s).')
    every = step_s // STEP_SECONDS
    interval = f'{czml_time(start)}/{czml_time(end)}'   # la misma ventana de la Etapa 4

    # Paquete "document": siempre el primero; fija el reloj de la escena
    czml = [{
        'id': 'document',
        'name': 'Satellite Orbit Explorer',
        'version': '1.0',
        'clock': {
            'interval': interval,
            'currentTime': czml_time(start),
            'multiplier': 300,                  # 300 s simulados por segundo real: 24 h en ~4,8 min
            'range': 'LOOP_STOP',               # al llegar al final, vuelve a empezar
            'step': 'SYSTEM_CLOCK_MULTIPLIER',  # avanza según el reloj real x multiplier
        },
    }]

    for el, pts, s, color in zip(elements, tracks, summaries, COLORS):
        samples = pts[::every]
        if samples[-1] is not pts[-1]:        # que la última muestra sea siempre el final de la ventana
            samples.append(pts[-1])
        coords = []                           # [t, lon, lat, alt_m, t, lon, lat, alt_m, ...]
        for p in samples:
            coords += [int((p['time'] - start).total_seconds()),
                       round(p['lon_deg'], 6), round(p['lat_deg'], 6),   # primero lon, luego lat
                       round(p['alt_km'] * 1000, 1)]                     # altura en METROS
        rgba = czml_rgba(color)
        period_s = round(s['period_h'] * 3600, 1)
        czml.append({
            'id': f"sat-{el['norad_id']}",
            'name': el['label'],
            'availability': interval,         # fuera de la ventana no se dibuja (nada de extrapolar)
            'description': czml_description(s),
            'position': {
                'epoch': czml_time(start),
                'interpolationAlgorithm': 'LAGRANGE',
                'interpolationDegree': 5,     # polinomio por 6 muestras vecinas
                'cartographicDegrees': coords,
            },
            'point': {
                'pixelSize': 8,
                'color': {'rgba': rgba},
                'outlineColor': {'rgba': [255, 255, 255, 255]},
                'outlineWidth': 1,
            },
            'label': {
                'text': el['name'],
                'font': '13px sans-serif',
                'fillColor': {'rgba': rgba},
                'outlineColor': {'rgba': [0, 0, 0, 255]},
                'outlineWidth': 2,
                'style': 'FILL_AND_OUTLINE',  # contorno negro: se lee sobre cualquier fondo
                'horizontalOrigin': 'LEFT',
                'pixelOffset': {'cartesian2': [10, 0]},
            },
            'path': {
                'leadTime': period_s,         # una vuelta por delante...
                'trailTime': period_s,        # ...y una por detrás
                'resolution': step_s,
                'width': 2,
                'material': {'solidColor': {'color': {'rgba': rgba}}},
            },
        })

    path.write_text(json.dumps(czml, separators=(',', ':')), encoding='utf-8')  # compacto
    n_samples = len(czml[1]['position']['cartographicDegrees']) // 4
    print(f'Saved {path} ({len(czml) - 1} satellites, {n_samples} samples each, every {step_s} s)')


export_czml(OUT_DIR / 'orbits.czml')

# 4) JSON para el visor 3D web (posiciones en ECEF y en ECI)
WEB_STEP_S = 60   # una posición cada 60 s, como el CZML
MODEL_NOTE = ('Two-body Kepler orbit from SGP4 mean elements, spherical Earth, '
              'simple Greenwich rotation - an educational approximation.')
SIDEREAL_DAY_H = 23.9345


def orbit_regime(s):
    """Régimen orbital a partir del periodo, la inclinación y la excentricidad."""
    if s['period_h'] < 2.5:                          # < ~2000 km de altura
        return 'LEO'
    if abs(s['period_h'] - SIDEREAL_DAY_H) < 0.1:   # geosíncrono: una vuelta por día sidéreo
        return 'GEO' if s['i_deg'] < 5 and s['e'] < 0.01 else 'IGSO'
    return 'MEO'


def ecef_from_spherical(lat_deg, lon_deg, alt_km):
    """(lat, lon, alt) sobre la Tierra esférica -> (x, y, z) ECEF en km. Inversa del paso 6 de propagate."""
    r = EARTH_RADIUS_KM + alt_km
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    return (r * math.cos(lat) * math.cos(lon),
            r * math.cos(lat) * math.sin(lon),
            r * math.sin(lat))


def eci_from_ecef(xf, yf, zf, t):
    """ECEF -> ECI girando +GMST alrededor de z: la inversa (traspuesta) de la rotación del paso 5.
    Sin precesión ni nutación, así que es un marco pseudo-inercial "de fecha", no ICRF exacto."""
    theta = gmst_rad(t)
    c, s = math.cos(theta), math.sin(theta)
    return (c * xf - s * yf,
            s * xf + c * yf,
            zf)


def export_web_json(path, step_s=WEB_STEP_S):
    """Escribe un JSON compacto para un visor 3D web: metadatos + un bloque por satélite con
    sus datos de órbita y sus posiciones cada step_s segundos en ECEF y en ECI (km, 1 decimal)."""
    if step_s % STEP_SECONDS:
        raise ValueError(f'step_s ({step_s} s) must be a multiple of STEP_SECONDS ({STEP_SECONDS} s).')
    every = step_s // STEP_SECONDS

    satellites = []
    for pts, s, color in zip(tracks, summaries, COLORS):
        samples = pts[::every]
        if samples[-1] is not pts[-1]:   # que la última muestra sea siempre el final de la ventana
            samples.append(pts[-1])

        ecef, eci = [], []
        for p in samples:
            xyz_f = ecef_from_spherical(p['lat_deg'], p['lon_deg'], p['alt_km'])
            xyz_i = eci_from_ecef(*xyz_f, p['time'])
            # Comprobación: una rotación conserva el radio (sin redondear, error numérico ~1e-9 km)
            assert abs(math.hypot(*xyz_i) - math.hypot(*xyz_f)) < 1e-6, f"{s['name']}: radius changed in ECI"
            ecef.append([round(v, 1) for v in xyz_f])
            eci.append([round(v, 1) for v in xyz_i])

        satellites.append({
            'name': s['name'],
            'norad_id': s['norad_id'],
            'regime': orbit_regime(s),
            'color': to_hex(color),          # 'tab:blue' -> '#1f77b4', el mismo que en las gráficas
            'period_h': round(s['period_h'], 4),
            'perigee_km': round(s['perigee_km'], 1),
            'apogee_km': round(s['apogee_km'], 1),
            'i_deg': round(s['i_deg'], 3),
            'e': round(s['e'], 7),
            'ecef': ecef,
            'eci': eci,
        })

    data = {
        'start_utc': f'{start:%Y-%m-%dT%H:%M:%SZ}',
        'step_s': step_s,
        'n_points': len(satellites[0]['ecef']),
        'units': 'km',
        'earth_radius_km': EARTH_RADIUS_KM,
        'frames': {
            'ecef': 'Earth-fixed, spherical Earth (x to 0 deg lon, z to North pole)',
            'eci': 'ECEF rotated by +GMST (IAU-1982); pseudo-inertial of date, no precession/nutation',
        },
        'source': source_note,
        'model_note': MODEL_NOTE,
        'satellites': satellites,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, separators=(',', ':'))   # compacto
    path.write_text(text, encoding='utf-8')
    print(f"Saved {path} ({len(satellites)} satellites, {data['n_points']} points each, "
          f"every {step_s} s, {path.stat().st_size / 1024:.0f} KB)")

    # Los mismos datos como script (.js): una web los carga con <script src=...>, que funciona
    # incluso abriendo el HTML con doble clic (file://), donde fetch() de un .json está bloqueado
    js_path = path.with_suffix('.js')
    js_path.write_text(f'window.ORBITS_WEB_DATA = {text};\n', encoding='utf-8')
    print(f'Saved {js_path} (same data as a script for the web viewer)')
    return data


web_data = export_web_json(PROJECT_DIR / 'outputs' / 'orbits_web.json')

# Comprobaciones del JSON (sobre los valores ya redondeados a 0,1 km)
for sat in web_data['satellites']:
    radius_diff = max(abs(math.hypot(*pi) - math.hypot(*pf)) for pi, pf in zip(sat['eci'], sat['ecef']))
    assert radius_diff < 0.2, f"{sat['name']}: ECI and ECEF radii differ by {radius_diff:.3f} km"
    print(f"Check passed: {sat['name']} ECI radius = ECEF radius (max diff {radius_diff:.3f} km, rounding only).")

    if sat['norad_id'] == 38652:   # SES-5 es geoestacionario: casi quieto en ECEF, un círculo entero en ECI
        def max_drift(points):
            x0, y0, z0 = points[0]
            return max(math.dist((x0, y0, z0), p) for p in points)
        drift_ecef, drift_eci = max_drift(sat['ecef']), max_drift(sat['eci'])
        assert drift_ecef < 500, f'SES-5 moves {drift_ecef:.0f} km in ECEF: it should be almost fixed'
        assert drift_eci > 80000, f'SES-5 moves only {drift_eci:.0f} km in ECI: it should circle the Earth'
        print(f'Check passed: SES-5 moves at most {drift_ecef:.1f} km in ECEF '
              f'but {drift_eci:,.0f} km in ECI (a full GEO circle).')

    # Régimen esperado de los satélites del taller (con datos en directo no hay valor conocido: se omite)
    expected = {64541: 'LEO', 59598: 'MEO', 38652: 'GEO', 42738: 'IGSO'}.get(sat['norad_id'])
    assert expected is None or sat['regime'] == expected, \
        f"{sat['name']}: unexpected regime {sat['regime']} (expected {expected})"

# 5) Informe de texto
report_lines = [
    'Satellite Orbit Report - built during Space Career Week 2026, GNSS Academy',
    '',
    source_note,
    f'Window (UTC): {start:%Y-%m-%d %H:%M:%S} -> {end:%Y-%m-%d %H:%M:%S}, step {STEP_SECONDS} s, {n_points} points',
    f'Model: {MODEL_NOTE}',
    '',
    'Orbit summary',
    *summary_table(summaries),
    '',
    'Orbital elements',
    *elements_table(elements),
]
report_path = OUT_DIR / 'orbit_report.txt'
report_path.write_text('\n'.join(report_lines) + '\n', encoding='utf-8')
print(f'Saved {report_path}')

# Comprimir los seis resultados en un zip
OUTPUT_FILES = ['ground_tracks.png', 'velocity_timeseries.png',
                'orbit_tracks_ecef.kml', 'orbit_animation.kml', 'orbits.czml', 'orbit_report.txt']
missing = [name for name in OUTPUT_FILES if not (OUT_DIR / name).is_file()]
if missing:
    raise FileNotFoundError(f'Missing outputs (run the earlier cells): {missing}')
zip_path = OUT_DIR / 'satellite_orbit_outputs.zip'
with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
    for name in OUTPUT_FILES:
        zf.write(OUT_DIR / name, arcname=name)
print(f'Saved {zip_path} ({zip_path.stat().st_size / 1024:.0f} KB, {len(OUTPUT_FILES)} files)')

# %%
# --- Etapa 8: descargar el zip (Colab) ---
try:
    from google.colab import files
    files.download(str(OUT_DIR / 'satellite_orbit_outputs.zip'))
except ImportError:  # fuera de Colab: el zip ya está en tu disco
    print(f"Not running in Colab. The zip is at: {OUT_DIR / 'satellite_orbit_outputs.zip'}")

# %% [markdown]
# ## Lista de comprobación "Run all"
#
# - [ ] La Etapa 1 muestra la configuración y las dos carpetas salen con `exists: True`
# - [ ] La Etapa 2 lista los 4 satélites del GP JSON
# - [ ] La Etapa 3 muestra la tabla de elementos (a, e y los cuatro ángulos)
# - [ ] La Etapa 4 muestra la ventana, 17281 puntos y marca QZS-2 como antiguo
# - [ ] La Etapa 5 pasa las comprobaciones de GMST, Kepler y SES-5
# - [ ] La Etapa 6 muestra el resumen y pasa la comprobación del periodo de Galileo
# - [ ] La Etapa 7 muestra y guarda las dos figuras
# - [ ] La Etapa 8 guarda los 2 KML, el CZML, el informe y el zip, y lo descarga
# - [ ] La Etapa 8 guarda `outputs/orbits_web.json` y pasa las comprobaciones de radio ECI/ECEF y de SES-5
#
# **Abrir los KML en Google Earth Pro:** Archivo > Abrir y elige un archivo `.kml`
# (o arrástralo sobre el globo). `orbit_tracks_ecef.kml` muestra las órbitas en 3D
# a su altura real (aleja la vista para ver el anillo GEO). Con
# `orbit_animation.kml` aparece un deslizador de tiempo arriba a la izquierda:
# pulsa Reproducir para ver la animación y usa los ajustes del deslizador (icono
# de la llave inglesa) para cambiar la velocidad o mostrar toda la traza hasta el
# instante actual. Ojo: la versión web de Google Earth no admite `gx:MultiTrack`,
# así que la animación solo funciona en Google Earth Pro.
#
# **Abrir el CZML en CesiumJS:** en tu propia página, o en el ejemplo "CZML" de
# Cesium Sandcastle, carga el archivo con
# `viewer.dataSources.add(Cesium.CzmlDataSource.load('orbits.czml'))`. El reloj
# arranca al inicio de la ventana a x300; haz clic en un satélite para ver su
# ficha (NORAD, periodo, perigeo/apogeo, e, i) en el InfoBox.
