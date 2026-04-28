"""
WebGIS Municipal - Servidor Flask
Polígonos fixos no mapa, consulta por clique ou CPD
"""

import os
import sqlite3
import struct
import math
import json
import gzip
import time
from bisect import bisect_left
from flask import Flask, request, jsonify, render_template, Response

app = Flask(__name__)

GPKG_PATH  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dados", "municipio.gpkg")
LAYER      = "zona_viabilidade"
CENTER_UTM = (200235.6, 7503318.0)   # São Pedro — SIRGAS 2000 UTM 23S

# Cache do GeoJSON gerado na inicialização
_geojson_cache: bytes = b""   # bytes gzip
_geojson_index: dict  = {}    # cpd.upper() → properties dict
_cpd_sorted:    list  = []    # lista ordenada de CPDs para bisect O(log n)


# ── Conversão UTM 23S → WGS84 ─────────────────────────────────────────────────
def utm_to_latlon(easting, northing):
    a=6378137.0; e=0.0818191908426; e2=e*e; e4=e2*e2; e6=e4*e2; k0=0.9996
    e1=(1-math.sqrt(1-e2))/(1+math.sqrt(1-e2))
    x=easting-500000.0; y=northing-10000000.0
    lon_origin=(-180+(23-1)*6+3)
    M=y/k0; mu=M/(a*(1-e2/4-3*e4/64-5*e6/256))
    phi1=(mu+(3*e1/2-27*e1**3/32)*math.sin(2*mu)
             +(21*e1**2/16-55*e1**4/32)*math.sin(4*mu)
             +(151*e1**3/96)*math.sin(6*mu)
             +(1097*e1**4/512)*math.sin(8*mu))
    sp1=math.sin(phi1); cp1=math.cos(phi1); tp1=math.tan(phi1)
    N1=a/math.sqrt(1-(e*sp1)**2)
    T1=tp1**2; C1=e2*cp1**2/(1-e2)
    R1=a*(1-e2)/(1-(e*sp1)**2)**1.5; D=x/(N1*k0)
    lat=phi1-(N1*tp1/R1)*(D**2/2-(5+3*T1+10*C1-4*C1**2-9*e2)*D**4/24
        +(61+90*T1+298*C1+45*T1**2-252*e2-3*C1**2)*D**6/720)
    lon=(D-(1+2*T1+C1)*D**3/6
          +(5-2*C1+28*T1-3*C1**2+8*e2+24*T1**2)*D**5/120)/cp1
    return round(math.degrees(lat),6), round(math.degrees(lon)+lon_origin,6)


# ── Parser WKB ────────────────────────────────────────────────────────────────
def parse_polygon_wkb(wkb, endian, start=0):
    """
    Parse um único Polygon a partir de `start` no buffer `wkb`.
    Retorna (rings, centroide, próximo_offset) para permitir iteração em MultiPolygon.
    """
    num_rings = struct.unpack_from(endian + 'I', wkb, start + 5)[0]
    offset = start + 9
    rings = []
    cx_sum = cy_sum = cnt = 0
    for _ in range(num_rings):
        npts = struct.unpack_from(endian + 'I', wkb, offset)[0]
        offset += 4
        ring = []
        for _ in range(npts):
            x, y = struct.unpack_from(endian + 'dd', wkb, offset)
            offset += 16
            lat, lon = utm_to_latlon(x, y)
            ring.append([lon, lat])
            cx_sum += lon
            cy_sum += lat
            cnt += 1
        rings.append(ring)
    centroide = [round(cx_sum / cnt, 6), round(cy_sum / cnt, 6)] if cnt else None
    return rings, centroide, offset


def blob_to_geometry(blob):
    """
    Retorna (geom_type_str, coordinates, centroide) ou (None, None, None).
    Suporta Polygon (WKB type 3) e MultiPolygon completo (WKB type 6).
    """
    if not blob:
        return None, None, None
    try:
        flags = blob[3]
        env_code = (flags >> 1) & 0b0111
        env_size = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get(env_code, 0)
        wkb = blob[8 + env_size:]
        if len(wkb) < 6:
            return None, None, None
        endian = '<' if wkb[0] == 1 else '>'
        geom_type = struct.unpack_from(endian + 'I', wkb, 1)[0]

        if geom_type == 3:  # Polygon
            rings, centroide, _ = parse_polygon_wkb(wkb, endian, start=0)
            return "Polygon", rings, centroide

        if geom_type == 6:  # MultiPolygon — processa TODOS os sub-polígonos
            num_geoms = struct.unpack_from(endian + 'I', wkb, 5)[0]
            if num_geoms == 0:
                return None, None, None
            all_polys = []
            cx_sum = cy_sum = cnt = 0
            offset = 9
            for _ in range(num_geoms):
                sub_endian = '<' if wkb[offset] == 1 else '>'
                rings, sub_centroide, offset = parse_polygon_wkb(wkb, sub_endian, start=offset)
                all_polys.append(rings)
                if sub_centroide:
                    cx_sum += sub_centroide[0]
                    cy_sum += sub_centroide[1]
                    cnt += 1
            centroide = [round(cx_sum / cnt, 6), round(cy_sum / cnt, 6)] if cnt else None
            return "MultiPolygon", all_polys, centroide

    except Exception as e:
        print(f"[WebGIS] Erro ao parsear geometria: {e}")

    return None, None, None


# ── Construir cache GeoJSON + índice por CPD ──────────────────────────────────
def build_cache():
    global _geojson_cache, _geojson_index, _cpd_sorted
    print("[WebGIS] Carregando polígonos do banco...")
    t0 = time.time()

    conn = sqlite3.connect(GPKG_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(f"""
        SELECT fid, geom,
               _IMOVEIS_nrCPD            AS cpd,
               _IMOVEIS_nrCadDV          AS cad,
               _IMOVEIS_nmLogradouro     AS log,
               _IMOVEIS_nrImovel         AS num,
               _IMOVEIS_nmBairro         AS bairro,
               _IMOVEIS_nrLote           AS lote,
               _IMOVEIS_AreaTerreno      AS area,
               _IMOVEIS_ZonaViabilidade  AS zona,
               _IMOVEIS_Matricula        AS mat
        FROM {LAYER}
    """)
    rows = cur.fetchall()
    conn.close()

    features = []
    index = {}
    skipped = 0

    for row in rows:
        geom_type_str, coords, centroide = blob_to_geometry(row["geom"])
        if geom_type_str is None:
            skipped += 1
            continue

        props = {
            "fid":    row["fid"],
            "cpd":    row["cpd"]    or "",
            "cad":    row["cad"]    or "",
            "log":    row["log"]    or "",
            "num":    row["num"]    or "",
            "bairro": row["bairro"] or "",
            "lote":   row["lote"]   or "",
            "area":   row["area"],
            "zona":   row["zona"],
            "mat":    row["mat"]    or "",
            "cx":     centroide[0] if centroide else None,
            "cy":     centroide[1] if centroide else None,
        }

        features.append({
            "type":     "Feature",
            "id":       row["fid"],
            "geometry": {"type": geom_type_str, "coordinates": coords},
            "properties": props,
        })

        if props["cpd"]:
            index[props["cpd"].upper()] = props

    geojson = {"type": "FeatureCollection", "features": features}
    raw = json.dumps(geojson, separators=(',', ':')).encode()
    _geojson_cache = gzip.compress(raw, compresslevel=6)
    _geojson_index = index
    _cpd_sorted    = sorted(index.keys())

    print(f"[WebGIS] {len(features)} polígonos carregados em {time.time()-t0:.1f}s")
    print(f"[WebGIS] GeoJSON: {len(raw)/1024/1024:.1f} MB → {len(_geojson_cache)/1024/1024:.1f} MB gzip")
    if skipped:
        print(f"[WebGIS] AVISO: {skipped} imóvel(is) sem geometria válida ignorado(s)")


# ── Rotas ──────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    lat, lon = utm_to_latlon(*CENTER_UTM)
    return render_template("index.html", center_lat=lat, center_lon=lon)


@app.route("/api/lotes.geojson")
def lotes_geojson():
    """Serve o GeoJSON completo comprimido (cache em memória)."""
    return Response(
        _geojson_cache,
        mimetype="application/json",
        headers={
            "Content-Encoding": "gzip",
            "Cache-Control":    "public, max-age=3600",
        }
    )


@app.route("/api/cpd/<path:cpd>")
def buscar_cpd(cpd):
    """Busca por CPD — exata, prefixo O(log n) via bisect, fallback substring."""
    chave = cpd.strip().upper()

    if chave in _geojson_index:
        return jsonify({"encontrado": True, "imovel": _geojson_index[chave]})

    # Busca por prefixo com bisect O(log n)
    pos = bisect_left(_cpd_sorted, chave)
    sugestoes = []
    for key in _cpd_sorted[pos:pos + 20]:
        if key.startswith(chave):
            sugestoes.append(key)
        else:
            break

    # Fallback substring O(n) apenas se prefixo não encontrou nada
    if not sugestoes:
        sugestoes = [k for k in _cpd_sorted if chave in k][:10]

    if len(sugestoes) == 1:
        return jsonify({"encontrado": True, "imovel": _geojson_index[sugestoes[0]]})
    if sugestoes:
        return jsonify({"encontrado": False, "sugestoes": sugestoes[:10]})
    return jsonify({"encontrado": False, "sugestoes": []})


@app.route("/api/bairros")
def bairros():
    conn = sqlite3.connect(GPKG_PATH)
    cur  = conn.cursor()
    cur.execute(f"""SELECT DISTINCT _IMOVEIS_nmBairro FROM {LAYER}
                    WHERE _IMOVEIS_nmBairro IS NOT NULL AND _IMOVEIS_nmBairro!=''
                    ORDER BY 1""")
    result = [r[0] for r in cur.fetchall()]
    conn.close()
    return jsonify(result)


# ── Inicialização ──────────────────────────────────────────────────────────────
build_cache()

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
