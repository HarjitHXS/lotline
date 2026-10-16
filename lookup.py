"""Address -> location -> zoning district, overlays, sewer area. All from free RIGIS map services.

Why E-911 address points? A normal geocoder puts the point on the street centerline, which can land
on a zone boundary. E-911 points sit on the building, so the zone lookup is reliable.
"""
import re

import httpx

RIGIS = "https://services2.arcgis.com/S8zZg9pg23JUEexQ/arcgis/rest/services"
ADDRESS_POINTS = f"{RIGIS}/FACILITY_AddressPoints_E911domains/FeatureServer/5"
ZONING = f"{RIGIS}/RI_Zoning/FeatureServer/1"
OVERLAYS = f"{RIGIS}/RI_Zoning/FeatureServer/0"
SEWER = f"{RIGIS}/UTILITY_Sewered_Areas_spf/FeatureServer/0"
USB = f"{RIGIS}/PLAN_Urban_Services_Boundary/FeatureServer/0"
WATER = f"{RIGIS}/UTILITY_Water_Supply_Districts_spf/FeatureServer/0"

SUFFIX = {"STREET": "ST", "ROAD": "RD", "AVENUE": "AVE", "DRIVE": "DR", "LANE": "LN", "COURT": "CT",
          "PLACE": "PL", "CIRCLE": "CIR", "TERRACE": "TER", "BOULEVARD": "BLVD", "HIGHWAY": "HWY", "PIKE": "PKE"}


def _get(url: str, **params) -> dict:
    r = httpx.get(url + "/query", params={"f": "json", **params}, timeout=20)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    return data


def normalize(addr: str) -> str:
    a = re.sub(r"[.,#]", " ", addr.upper())
    a = re.sub(r"\b(CUMBERLAND|RI|RHODE ISLAND|0286\d)\b", " ", a)
    words = [SUFFIX.get(w, w) for w in a.split()]
    return " ".join(words).strip()


def find_address(addr: str, town: str = "CUMBERLAND", limit: int = 8) -> list[dict]:
    q = normalize(addr).replace("'", "''")
    where = f"municipality='{town}' AND fulladdr LIKE '{q}%'"
    data = _get(ADDRESS_POINTS, where=where, outFields="fulladdr,zipcode,pointtype",
                returnGeometry="true", outSR=4326, resultRecordCount=limit)
    seen, out = set(), []
    for f in data.get("features", []):
        a = f["attributes"]["fulladdr"]
        if a in seen:
            continue
        seen.add(a)
        out.append({"address": a, "zip": f["attributes"]["zipcode"],
                    "lon": f["geometry"]["x"], "lat": f["geometry"]["y"]})
    return out


def _at(url: str, lon: float, lat: float, fields: str = "*") -> list[dict]:
    data = _get(url, geometry=f"{lon},{lat}", geometryType="esriGeometryPoint", inSR=4326,
                spatialRel="esriSpatialRelIntersects", outFields=fields, returnGeometry="false")
    return [f["attributes"] for f in data.get("features", [])]


def site_facts(lon: float, lat: float) -> dict:
    zones = _at(ZONING, lon, lat, "ZONE_CODE,ZONE_DESC,MIN_LOT_SF,MUNI_NAME")
    overlays = _at(OVERLAYS, lon, lat, "OVERLAY,ZONE_DESC,SOURCE_ORD,SOURCE_DATE")
    sewer = _at(SEWER, lon, lat, "WWTF")
    usb = _at(USB, lon, lat, "OBJECTID")
    try:
        water = sorted({w["SUPPLIER"] for w in _at(WATER, lon, lat, "SUPPLIER") if w.get("SUPPLIER")})
    except Exception:
        water = []
    return {
        "zone": zones[0]["ZONE_CODE"] if zones else None,
        "zone_desc": (zones[0]["ZONE_DESC"] or "").strip() if zones else None,
        "all_zones": [z["ZONE_CODE"] for z in zones],
        "overlays": [o["ZONE_DESC"] for o in overlays],
        "sewer_area": bool(sewer),
        "sewer_plant": sewer[0]["WWTF"] if sewer else None,
        "in_urban_services_boundary": bool(usb),
        "water_suppliers": water,
        "map_source": "RIGIS Rhode Island Zoning and Overlay Zones (generalized; confirm on the town's official zoning map)",
    }


if __name__ == "__main__":
    import sys

    for m in find_address(" ".join(sys.argv[1:]) or "25 School St"):
        print(m["address"], site_facts(m["lon"], m["lat"]))
